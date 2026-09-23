"""译文的切段规则与外部翻译服务调用。

这个模块**不做翻译**：翻译一律交给用户配置的翻译服务（「模型服务」里已连接的平台，
或用户自己填写的兼容接口）。这里只负责两件与平台无关的事：

- **切段**：把一页文字切成稳定编号的段落/句子。编号只由该页当前文本决定，
  所以"直接复用上次译文"是精确命中，而不是模糊匹配。
- **调用**：逐段发给服务，任何一段失败都如实抛出，**不会用原文冒充译文**。
"""
import hashlib
import re

# 句末标点：碰到就断句，而不是把下一句接上来。
# **分号不算句末**：中文里"…的倾向；又要防止…"是同一句的两个分句，按分号切开就会把一句话
# 从中间劈成两段（用户截图里那段译文正是这样被截断的）。冒号同理。
_SENTENCE_END = '。！？….!?'
_CLOSERS = '）)】]》”’"\''
_MARKER_START = re.compile(r'^(?:\[\d{1,3}\]|\[\s*[A-Za-z]\s*\d{1,3}\]|\d{1,3}[.)、]|[•·▪◦*\-–—]|\(\d{1,3}\)|（[一二三四五六七八九十]{1,3}）)')
_SECTION_HEADING = re.compile(r'^(?:第[一二三四五六七八九十百\d]{1,4}[章节]|\d+(?:\.\d+){1,3}\s*\S|(?:abstract|introduction|related work|methods?|materials and methods|results?|discussion|conclusions?|references|acknowledg(?:e)?ments?)\b)', re.I)
_CJK = re.compile(r'[\u3400-\u9fff]')

MAX_SEGMENT_CHARS = 1200
# 并发段数：一段一次请求，串行发一页要等十几次往返（用户报"整页翻译非常慢"）。
# 4 是本机与常见云平台都稳的值；失败与取消的语义不变（任意一段失败仍然整体如实报错）。
TRANSLATE_WORKERS = 4


def normalize(text):
    """比对用的归一化：折叠空白，统一引号与破折号，但不改字母与数字。"""
    out = str(text or '').replace('\r\n', '\n').replace('\r', '\n')
    for source, target in (('\u00a0', ' '), ('\u3000', ' '), ('“', '"'), ('”', '"'), ('‘', "'"), ('’', "'"), ('–', '-'), ('—', '-'), ('−', '-')):
        out = out.replace(source, target)
    return re.sub(r'\s+', ' ', out).strip()


def source_hash(text):
    return hashlib.sha256(normalize(text).encode('utf-8')).hexdigest()[:16]


def detect_language(text):
    """只用字符构成判断，不调用任何服务；判不出来就如实说不知道。"""
    letters = sum(1 for char in text if char.isalpha())
    if not letters:
        return 'unknown'
    return 'zh' if len(_CJK.findall(text)) / letters > .3 else 'latin'


def _ends_sentence(text, index):
    """句子在 index 处结束，且后面的收尾字符（引号/括号）也算这一句的。"""
    char = text[index]
    if char == '.' and index + 1 < len(text) and text[index + 1].isdigit():
        return False            # 小数点/版本号，不是句末
    if char == '.' and index >= 1 and text[index - 1].isupper() and (index + 1 >= len(text) or text[index + 1].isspace()):
        return False            # 缩写（U.S. A.）
    end = index + 1
    while end < len(text) and text[end] in _CLOSERS:
        end += 1
    if end >= len(text):
        return True
    rest = text[end:]
    # 中文句号后面通常**不**带空格，所以除了空白/数字，还要认"紧跟下一个中文字符"。
    # （漏了这一条时"第一句。第二句。"会被当成一句话。）
    return not rest or rest[0].isspace() or rest[0].isdigit() or bool(_CJK.match(rest[0]))


def split_sentences(block):
    """把一段文字切成句子；不改变任何字符，只决定在哪里断开。"""
    text = normalize(block)
    if not text:
        return []
    pieces = []
    start = 0
    index = 0
    while index < len(text):
        if text[index] in _SENTENCE_END and _ends_sentence(text, index):
            end = index + 1
            while end < len(text) and text[end] in _CLOSERS:
                end += 1
            pieces.append(text[start:end].strip())
            start = end
            index = end
            continue
        index += 1
    tail = text[start:].strip()
    if tail:
        pieces.append(tail)
    return [piece for piece in pieces if piece]


def _hard_split(text, limit=MAX_SEGMENT_CHARS):
    """超长句按标点/空格切成不超过 limit 的片段，绝不截断单词。"""
    pieces = []
    rest = text.strip()
    while len(rest) > limit:
        window = rest[:limit]
        cut = max(window.rfind('，'), window.rfind(','), window.rfind('；'), window.rfind(';'), window.rfind(' '))
        if cut < limit // 2:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


def _join(previous, line):
    """按中英混排惯例拼行：英文断词接上，英文之间补空格，中文之间不补。"""
    if not previous:
        return line
    if previous.endswith('-') and line[:1].isalnum() and line[:1].isascii():
        return previous[:-1] + line
    left, right = previous[-1], line[0]
    if left.isascii() and left.isalnum() and right.isascii() and right.isalnum():
        return previous + ' ' + line
    return previous + line


def split_segments(text):
    """把一页阅读文本切成稳定编号的段落单元。

    先用 ``reflow_lines`` 的保守规则合并排版硬换行（英文断词、标题、列表、图表题注
    都不合并），**一段就是一整段**：段内的句子不拆开。只有超长的段落
    （>1200 字符）才按句号切开，避免单次请求过大——**切开的那几段带着同一个 ``para`` 编号**，
    界面据此把它们渲染成一个自然段（用户反馈过"段落中间阶段分为两部分翻译"：切开的段落在
    界面上必须看起来仍是一段）。

    编号只取决于这一页的文本本身，因此"重新翻译"不会让段落错位。
    """
    from .literature_core.structure import reflow_lines
    segments = []
    paragraph_index = 0
    for paragraph in reflow_lines(text).split('\n\n'):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= MAX_SEGMENT_CHARS:
            segments.append((paragraph_index, paragraph))
            paragraph_index += 1
            continue
        buffer = ''
        for sentence in split_sentences(paragraph) or [paragraph]:
            for piece in _hard_split(sentence):
                if buffer and len(buffer) + len(piece) <= MAX_SEGMENT_CHARS:
                    buffer = _join(buffer, piece)
                    continue
                if buffer:
                    segments.append((paragraph_index, buffer))
                buffer = piece
        if buffer:
            segments.append((paragraph_index, buffer))
        paragraph_index += 1
    return [{'index': index, 'text': segment, 'para': para}
            for index, (para, segment) in enumerate(segments)]


SYSTEM_PROMPT = (
    '你是学术文献翻译助手。只输出译文，不要解释、不要前后缀、不要 Markdown 代码块。'
    '严格保持原文的段落、编号、术语与语气；数字、公式、文献引用编号（如 [12]）必须原样保留。'
    '专有名词首次出现时可写「中文（English）」，其后只用中文。'
)
USER_PROMPT = '请把下面这段{source}文本翻译成{target}。\n\n【原文】\n{text}'


def target_label(language):
    return {'zh': '简体中文', 'en': 'English', 'ja': '日本語', 'ko': '한국어', 'fr': 'Français',
            'de': 'Deutsch', 'es': 'Español', 'ru': 'Русский'}.get((language or '').lower(), language or '简体中文')


def build_prompt(text, language):
    """给翻译服务的提示词：只包含这一段原文，不带任何本机信息。"""
    return USER_PROMPT.format(source='英文' if detect_language(text) == 'latin' else '', target=target_label(language), text=text)


def strip_wrapper(raw):
    """模型常把译文包在代码围栏或「译文：」前缀里；这里只剥掉这层壳，不改内容。"""
    text = (raw or '').strip()
    fenced = re.fullmatch(r'```[A-Za-z]*\s*\n([\s\S]*?)\n?```', text)
    if fenced:
        text = fenced.group(1).strip()
    text = re.sub(r'^(?:译文|翻译|Translation|Translated text)\s*[:：]\s*', '', text).strip()
    return text


TERMINAL = '。！？!?…"』」）)]'
SOURCE_TERMINAL = '.!?。！？…"』」）)]'


def looks_unfinished(source, text):
    """译文看起来没翻完——只做**保守**判断，宁可不报也不误报。

    用户报过"段落有时候会从中间截断"：真因是翻译请求没给输出预算，落到服务端默认上限后
    被截断，而不少兼容服务在这种情况下仍返回 ``finish_reason='stop'``（我们无法据此报错）。
    这里用两条保守信号兜底：原文以句末标点结束、译文既没有句末标点、又**远**短于原文
    （不足原文可见字符数的 25%——英译中正常也有 45%~60%，被截断的通常只剩一两成）。
    """
    src = normalize(source)
    out = normalize(text)
    if len(src) < 120 or not out:
        return False
    if src[-1] not in SOURCE_TERMINAL:
        return False
    if out[-1] in TERMINAL:
        return False
    return len(out) < len(src) * 0.25


def _sentences_with_offsets(text):
    """句子 + 它在原文里的字符位置（用于把"选中的一小段"落回句子上）。"""
    out = []
    cursor = 0
    for sentence in split_sentences(text):
        at = text.find(sentence, cursor)
        if at < 0:
            at = cursor
        out.append({'text': sentence, 'start': at, 'end': at + len(sentence)})
        cursor = at + len(sentence)
    return out


def sentence_span(text, part):
    """``part`` 落在 ``text`` 的哪几个句子上；找不到返回 None。"""
    part = (part or '').strip()
    if not part:
        return None
    at = text.find(part)
    if at < 0:
        return None
    end = at + len(part)
    sentences = _sentences_with_offsets(text)
    picked = [index for index, item in enumerate(sentences) if item['start'] < end and item['end'] > at]
    if not picked:
        return None
    return picked[0], picked[-1]


def map_sentence_span(source, text, part):
    """同 :func:`map_sentences`，但返回 ``text`` 里的 ``(start, end, exact)`` 字符区间。

    界面要在译文里**按区间标出**那一段，所以必须给出原文里的真实位置：先按句子对齐取到那几句，
    再用句子的字符偏移量切出区间（而不是把句子重新拼一遍——拼接出来的字符串可能与译文里的
    空白不同，`indexOf` 就找不到，界面只能退回"整段都标"）。
    对不上返回 None。
    """
    span = sentence_span(source, part)
    if span is None:
        return None
    left = _sentences_with_offsets(source)
    right = _sentences_with_offsets(text)
    if not left or not right:
        return None
    start, stop = span
    if len(left) == len(right):
        picked, exact = right[start:stop + 1], True
    else:
        first = min(len(right) - 1, int(start / len(left) * len(right)))
        last = max(first, min(len(right) - 1, int((stop + 1) / len(left) * len(right)) - 1))
        picked, exact = right[first:last + 1], False
    if not picked:
        return None
    return picked[0]['start'], picked[-1]['end'], exact


def map_sentences(source, text, part):
    """把 ``part``（source 里的一段）映射成 ``text`` 里对应的一段，返回 ``(片段, 是否精确)``。

    做法是**句级对齐**：先看 part 覆盖了 source 的哪几句，再取 text 里相同序号的句子。
    两边的句数相同就是精确映射；不同（译文常把两句合成一句、或把一句拆开）就按句子序号的比例
    取一段，仍然保证是完整句子。返回的片段是 ``text`` 的**真子串**（见 :func:`map_sentence_span`）。

    为什么需要它：用户要求"能对译文里的一部分内容批注"，而批注**必须落在原文上**。没有词级
    对齐数据，句级对齐是能做到的、可解释的最细粒度——它比"整段"精确，又不会像按字数比例硬切
    那样给出半句话、让引用位置算错。
    """
    span = map_sentence_span(source, text, part)
    if span is None:
        return part, False
    start, end, exact = span
    return text[start:end], exact


def remote_translate_batch(segments, profile, language, check=None, progress=None):
    """并发翻译多段，返回 ``(成功的译文, 错误列表)``。

    和 :func:`remote_translate` 的区别只在失败时：这里**保住已经翻好的段**，
    让"一段失败不要丢掉整页"这条既有行为在并发下依然成立（调用方会照实报告错误）。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from threading import Lock

    from . import translation_protocols
    if not (profile and profile.get('base_url')):
        raise ValueError('未配置翻译服务地址')
    items = list(segments)
    output = {}
    errors = []
    if not items:
        return output, errors
    lock = Lock()
    done = 0

    def run(segment):
        if check:
            check()
        # 系统提示与用户提示一起交给协议层：{system_prompt, prompt} 两个键就是全部输入。
        request = {**profile, 'system_prompt': SYSTEM_PROMPT, 'prompt': build_prompt(segment['text'], language)}
        cleaned = strip_wrapper(translation_protocols.translate(request, language))
        if not cleaned:
            raise RuntimeError('翻译服务返回了空译文')
        if check:
            check()
        return segment['index'], cleaned

    def record(position):
        nonlocal done
        done = position
        if progress:
            with lock:                     # progress 会写任务状态，多线程下必须串行
                progress(done, len(items))

    workers = max(1, min(TRANSLATE_WORKERS, len(items)))
    if workers == 1:
        for position, segment in enumerate(items, start=1):
            record(position)
            try:
                index, text = run(segment)
            except Exception as error:      # noqa: BLE001 —— 如实收集，不吞
                errors.append(error)
                continue
            output[index] = text
        return output, errors

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run, segment): segment for segment in items}
        for future in as_completed(futures):
            try:
                index, text = future.result()
            except Exception as error:      # noqa: BLE001 —— 单段失败不影响其它段
                errors.append(error)
                continue
            output[index] = text
            record(len(output))
    return output, errors


def remote_translate(segments, profile, language, check=None, progress=None):
    """逐段调用配置好的翻译服务。任何一段失败都如实抛出，不会用原文冒充译文。

    段与段之间彼此独立，因此"只重译这一段"与"整页重译"走的是同一条路；也正因为彼此独立，
    这里**并发发送**（默认 4 段同时），否则一页十几段要串行等十几次往返——用户报过
    "整页翻译的速度非常慢"。需要"部分成功也保留"的调用方请用 :func:`remote_translate_batch`。
    """
    output, errors = remote_translate_batch(segments, profile, language, check=check, progress=progress)
    if errors:
        raise errors[0]
    return output
