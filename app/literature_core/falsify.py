"""证伪检索（Phase 5 的姊妹，§36）：主动去找**反对、限制、修正**当前结论的原文。

要防的失败方式：系统读完几页、看到作者反复论证 X，就回答"作者认为 X"——而作者可能在第 2 页
加过限定、在第 9 页反驳过自己先前的说法、把某个反例当作例外。"读到支持 X 的段落"这件事本身
不会让人想到去读反面，于是回答**看着有据、实际只读了一半**。

与缺口检索（`chat_feature.gap_search`）的分工：

- 缺口检索是**读完之后**的覆盖核查（"要谈的方面我都查过了吗"），确认性质；
- 证伪检索是**读之前**的反向召回（"如果作者其实反对或限定过，那会在哪几页"），发现性质。

它靠两层信号，**两层都是确定性的、都不调模型**：

1. **转折标记**（本模块）：中文的"然而/但是/不过/相反/反之/另一方面/尽管如此/例外/反例/质疑/批评/
   修正/更正/补充说明"、英文的 `however/although/nevertheless/on the contrary/conversely/exception/
   counterexample/qualification/criticism/revise`……这些词本身就是"接下来要唱反调"的信号；
2. **语义反向召回**（`literature_core/semantic.py`）：用"X 的反面与异议"这类反向表述去比对，
   抓那些**没有**用标记词、但整段都在讲反面的页。

本模块只做第 1 层与命中判定；第 2 层由调用方把页并进来。
"""
import re

# 转折与自省标记。**同一份清单同时用于两件事**：挑页（哪几页有反面迹象）与自证
# （那一页里到底有没有反面句）。两处用两份清单迟早会走偏——一处改了另一处没改，
# 就会出现"说找到了反面、点开却看不到"的情况。
CONTRAST_MARKERS = (
    # 中文
    '然而', '但是', '但', '不过', '相反', '反之', '另一方面', '尽管如此', '话虽如此', '未必',
    '并不', '并非', '例外', '反例', '反证', '质疑', '批评', '批判', '反驳', '修正', '更正',
    '更正为', '修正为', '有待商榷', '值得商榷', '局限', '限定', '前提是', '仅在', '只能',
    # 英文（大小写不敏感）
    'however', 'although', 'though', 'nevertheless', 'nonetheless', 'on the contrary', 'conversely',
    'in contrast', 'exception', 'counterexample', 'counter-example', 'qualification', 'qualifies',
    'criticism', 'critique', 'objection', 'rebuttal', 'revise', 'revised', 'correction', 'erratum',
    'does not', 'cannot', 'fails to', 'only when', 'only if',
)
# 标记词里的"太常见、单独出现说明不了问题"的那些：中文单字"但"在正文里极常见，
# 只见它一次不足以说明这一页在唱反调。判"这一页有反面迹象"时要求**非弱标记**命中。
_WEAK_MARKERS = ('但', '不过', 'does not', 'cannot')

_SENTENCE_SPLIT = re.compile(r'[。！？；\n]|(?<=[.!?;])\s+')
DEFAULT_LIMIT = 8


def _normalize(text):
    """比对口径与安全网一致：去掉空白、转小写。"""
    return re.sub(r'\s+', '', text or '').lower()


def markers_in(text, strong_only=False):
    """这段文字里出现了哪些转折/自省标记（按 `CONTRAST_MARKERS` 顺序，去重）。

    `strong_only=True` 时排除 `_WEAK_MARKERS`：判"这一页有反面迹象"用这一档，
    因为中文的"但"、英文的 "does not" 在普通陈述里到处都是，不足以当证据。
    """
    body = _normalize(text)
    if not body:
        return []
    found = []
    for marker in CONTRAST_MARKERS:
        if strong_only and marker in _WEAK_MARKERS:
            continue
        if _normalize(marker) in body:
            found.append(marker)
    return found


def contrast_pages(rows, limit=DEFAULT_LIMIT):
    """哪些页有**反面迹象**（`{页码: [标记, ...]}`，页码升序）。

    只有"非弱标记"才算迹象：一页里出现"然而/相反/反例/however/exception"这类词，
    说明作者在这一页很可能正在提出限制或反驳。
    """
    hits = {}
    for page, text in rows or []:
        if not isinstance(page, int) or page < 1 or not text:
            continue
        strong = markers_in(text, strong_only=True)
        if strong:
            hits.setdefault(page, strong)
    ordered = dict(sorted(hits.items()))
    if limit and len(ordered) > limit:
        ordered = dict(list(ordered.items())[:limit])
    return ordered


def counter_sentences(text, limit=3, size=200):
    """这一页里**含反面标记的那几句**（逐句给出，供报告展示）。

    为什么要给句子而不是只说"第 7 页有反例"：用户要能一眼核对"这条反对意见是不是真的"。
    句子直接从原文切出来，不改写、不翻译、不补全；找不到标记句时返回空列表
    ——**宁可说"这一页有标记词但切不出句子"，也不编一句**。
    """
    if not text:
        return []
    out = []
    for sentence in _SENTENCE_SPLIT.split(text):
        cleaned = (sentence or '').strip()
        if len(cleaned) < 8:
            continue
        if markers_in(cleaned, strong_only=True):
            out.append(cleaned[:size])
        if len(out) >= limit:
            break
    return out


def falsification_report(rows, scope, extra_pages=(), limit=DEFAULT_LIMIT, queries=(), note=''):
    """一次证伪检索的完整记录：查了什么、哪几页有反面迹象、补了哪几页、逐页给一句证据。

    与 `sweep_report` 一样，**每个数字都是真的**：`contrast_pages` 是标记命中的页，
    `semantic_pages` 是反向语义召回并进来的页，`evidence` 是逐页切出来的反面句
    （切不出来的页如实留空，不编）。`added_pages` 是因此**真的被读进来**的页。
    """
    read = {page for page in (scope or []) if isinstance(page, int)}
    # `limit` 只限制**补进来**的页数，不限制"标出多少页有反面迹象"：
    # 台账要如实说"这次一共在几页上看到了反面迹象"，截断只影响读多少。
    lexical = contrast_pages(rows, limit=0)
    semantic = sorted({page for page in (extra_pages or ()) if isinstance(page, int) and page >= 1})
    marked = set(lexical) | set(semantic)
    outside = [page for page in sorted(marked) if page not in read]
    added = outside[:limit] if limit else outside
    texts = {page: text for page, text in rows or [] if isinstance(page, int)}
    evidence = {}
    for page in sorted(marked):
        sentences = counter_sentences(texts.get(page) or '')
        if sentences:
            evidence[str(page)] = sentences
    return {
        'source': '转折标记 + 反向语义',
        'queries': [query for query in dict.fromkeys(queries or ()) if isinstance(query, str) and query.strip()],
        'scanned_pages': len(texts),
        'contrast_pages': sorted(lexical),
        'semantic_pages': semantic,
        'marked_pages': sorted(marked),
        'added_pages': added,
        'evidence': evidence,
        'truncated': max(0, len(outside) - len(added)),
        'note': note,
    }


def opposite_queries(terms, original, limit=3):
    """从问题与术语构造**反向**检索词："X 的反面/限制/异议"。

    只用模板拼接，**不调模型**：证伪检索的价值在于"系统主动想了反面"，
    而这件事不需要模型——需要的是每次都真的去做。
    """
    base = []
    for term in list(terms or ()) + [original]:
        if isinstance(term, str) and len(term.strip()) >= 2 and term.strip() not in base:
            base.append(term.strip())
    out = []
    for term in base[:limit]:
        for suffix in ('的反面与反例', '的限定条件', '受到的质疑与批评'):
            query = term + suffix
            if query not in out:
                out.append(query)
    return out[:limit]
