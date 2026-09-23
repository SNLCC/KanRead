"""译文配置、存储与整页/整篇翻译流程。

存储口径（对应需求第 3 条）：

- 译文**逐段**保存，键是（文献、页、段号）。段号只由该页当前阅读文本决定，
  所以"直接调用上次结果"是精确命中，而不是模糊匹配。
- 每段保存**它翻译时对应的原文**与内容哈希。原文或页校订变了，这段就标记为
  已过期（``stale``）并重新翻译，而不是把旧译文贴到新原文上。
- 换翻译服务时旧译文默认保留；只有用户点了"重新翻译（覆盖）"才用新服务覆盖。
  覆盖会清空手动修改标记——那是旧引擎的产物，继续声称"人工修改过"会误导。
"""
import json
import re
import threading
import time

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS translation_segments(
  id TEXT PRIMARY KEY, document_id TEXT, page INTEGER, segment INTEGER,
  source TEXT, source_hash TEXT, text TEXT, engine TEXT, service TEXT,
  terms TEXT, unknown_terms TEXT, coverage REAL, edited INTEGER DEFAULT 0,
  origin TEXT DEFAULT 'machine', previous_text TEXT DEFAULT '', previous_origin TEXT DEFAULT '',
  created TEXT DEFAULT CURRENT_TIMESTAMP,
  updated TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE UNIQUE INDEX IF NOT EXISTS translation_segment_key ON translation_segments(document_id,page,segment);
CREATE INDEX IF NOT EXISTS translation_page ON translation_segments(document_id,page);
CREATE TABLE IF NOT EXISTS translation_pages(
  document_id TEXT, page INTEGER, source_hash TEXT, engine TEXT,
  status TEXT DEFAULT 'pending', error TEXT DEFAULT '', done INTEGER DEFAULT 0,
  total INTEGER DEFAULT 0, updated TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(document_id,page));
"""

# 内置引擎与外部引擎在设置里是两种"服务"。显示名只描述调用方式，不暗示任何官方合作。
LOCAL_ENGINE = 'local'
REMOTE_ENGINE = 'model'
# 旧版本的 engine 列含义保留（表结构不变，旧数据仍可读）；现在只会有 REMOTE_ENGINE。
ENGINE_LABELS = {REMOTE_ENGINE: '翻译服务'}

_JOBS = {}
_JOBS_LOCK = threading.RLock()


def ensure_tables(conn):
    conn.executescript(TABLE_SQL)
    # 旧库补列：CREATE TABLE IF NOT EXISTS 不会给已存在的表加字段，
    # 与 main.py 里那几处 ALTER 同一口径（PRAGMA 守护 + 带默认值），旧数据保持可读。
    columns = {row[1] for row in conn.execute('PRAGMA table_info(translation_segments)')}
    for name, definition in (('previous_text', "TEXT DEFAULT ''"), ('previous_origin', "TEXT DEFAULT ''")):
        if name not in columns:
            conn.execute(f'ALTER TABLE translation_segments ADD COLUMN {name} {definition}')


def _host_db(host):
    return host.db()


def read_text(host, doc_id, page, meta=None, path=None):
    """这一页当前的阅读文本：用户校订优先，其次是解析/识别结果。"""
    from app import parsing
    from app.reading_adapter import extracted_page
    meta = meta or host.document(doc_id)
    path = path or host.document_path(meta)
    saved = parsing.get(doc_id, page, path)
    if saved is not None:
        return saved[0], saved[1]
    stat = path.stat()
    return extracted_page(str(path), (stat.st_size, stat.st_mtime_ns), page, True)


def _clean_box(row):
    """把定位返回的一行结果规整成 [x0,y0,x1,y1]；形态不符就返回 None。

    定位这条链路会返回两种形态（``{'bbox': [...], 'text': ...}`` 或直接 ``[...]``），
    而**绝不能**假定坐标一定是数字：OCR 行框来自数据库、字形框来自页面数据，
    任何一份脏数据都不该让整页翻译失败（曾经的 500 就是这么来的）。
    """
    box = row.get('bbox') if isinstance(row, dict) else row
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    values = []
    for value in box:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        values.append(float(value))
    return values


def page_boxes(host, doc_id, page, quotes, meta=None, path=None):
    """把每段原文映射到版面上的合并框；定位不到就如实返回空框。

    复用既有的引用定位（按行匹配，双栏重排与 OCR 行框都成立），因此不新增定位逻辑、
    也不触发新的识别：只用文本层与已有缓存。

    这个函数**只影响界面上的框**，所以整段定位都包在 try 里：定位失败时如实返回
    "未定位"，而不是让整页翻译跟着一起失败。
    """
    from app import citation
    from app import parsing
    from app.reading_adapter import native_page, ocr_layout
    meta = meta or host.document(doc_id)
    path = path or host.document_path(meta)
    stat = path.stat()
    version = (stat.st_size, stat.st_mtime_ns)
    try:
        data = host.page_result(doc_id, page)
    except Exception:
        data = {}
    text, origin = read_text(host, doc_id, page, meta, path)
    lines = parsing.get_boxes(doc_id, page, path)
    if not lines and origin == 'ocr':
        lines = ocr_layout(str(path), version, page)
    if not data.get('spans') and not lines:
        data = {**data, **native_page(str(path), version, page)}
    width, height = data.get('width'), data.get('height')
    result = []
    for quote in quotes:
        boxes = []
        try:
            # 注意：locate_page_text 返回 (每行的框, 定位方式)，不是框列表本身。
            located, _method = citation.locate_page_text(text, quote, width, height, data.get('spans'), lines)
            for row in located or []:
                clean = _clean_box(row)
                if clean:
                    boxes.append(clean)
        except Exception:
            boxes = []
        if boxes:
            result.append({'bbox': [min(b[0] for b in boxes), min(b[1] for b in boxes),
                                    max(b[2] for b in boxes), max(b[3] for b in boxes)],
                           'boxes': boxes, 'located': True})
        else:
            result.append({'bbox': None, 'boxes': [], 'located': False})
    return result


def stored_segments(host, doc_id, page):
    with _host_db(host) as c:
        return [dict(row) for row in c.execute(
            'SELECT * FROM translation_segments WHERE document_id=? AND page=? ORDER BY segment', (doc_id, page))]


def page_state(host, doc_id, page):
    with _host_db(host) as c:
        row = c.execute('SELECT * FROM translation_pages WHERE document_id=? AND page=?', (doc_id, page)).fetchone()
    return dict(row) if row else None


def page_states(host, doc_id):
    with _host_db(host) as c:
        return [dict(row) for row in c.execute('SELECT * FROM translation_pages WHERE document_id=? ORDER BY page', (doc_id,))]


def _upsert(host, doc_id, page, index, values, source, hash_value, engine, service):
    with _host_db(host) as c:
        previous = c.execute('SELECT text,origin FROM translation_segments WHERE document_id=? AND page=? AND segment=?',
                             (doc_id, page, index)).fetchone()
        machine = values['text']
        origin = values.get('origin') or 'machine'
        if origin == 'machine' and previous and previous['origin'] == 'user' and previous['text'] != machine:
            # 机器重新翻译了一段被人工改过的内容：把人工那次改动留作可恢复的原稿，
            # 这样"恢复机器译文"能真的回到上一个版本，而不是一句空话。
            c.execute("UPDATE translation_segments SET previous_text=text,previous_origin=origin WHERE document_id=? AND page=? AND segment=?",
                      (doc_id, page, index))
        c.execute("""INSERT INTO translation_segments(id,document_id,page,segment,source,source_hash,text,engine,service,terms,unknown_terms,coverage,edited,origin,updated)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                     ON CONFLICT(document_id,page,segment) DO UPDATE SET
                       source=excluded.source,source_hash=excluded.source_hash,text=excluded.text,
                       engine=excluded.engine,service=excluded.service,terms=excluded.terms,
                       unknown_terms=excluded.unknown_terms,coverage=excluded.coverage,
                       edited=excluded.edited,origin=excluded.origin,updated=CURRENT_TIMESTAMP""",
                  (host.uuid.uuid4().hex, doc_id, page, index, source, hash_value, machine, engine, service,
                   host.json.dumps(values.get('terms') or [], ensure_ascii=False),
                   host.json.dumps(values.get('unknown_terms') or [], ensure_ascii=False),
                   float(values.get('coverage') or 0), int(values.get('edited') or 0), origin))


def _set_state(host, doc_id, page, **fields):
    current = page_state(host, doc_id, page) or {}
    merged = {'document_id': doc_id, 'page': page, 'source_hash': '', 'engine': '', 'status': 'pending',
              'error': '', 'done': 0, 'total': 0, **current, **fields}
    with _host_db(host) as c:
        c.execute("""INSERT INTO translation_pages(document_id,page,source_hash,engine,status,error,done,total,updated)
                     VALUES(?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP)
                     ON CONFLICT(document_id,page) DO UPDATE SET
                       source_hash=excluded.source_hash,engine=excluded.engine,status=excluded.status,
                       error=excluded.error,done=excluded.done,total=excluded.total,updated=CURRENT_TIMESTAMP""",
                  (doc_id, page, merged['source_hash'], merged['engine'], merged['status'], merged['error'],
                   int(merged['done'] or 0), int(merged['total'] or 0)))
    return merged


def engine_profile(host):
    """翻译服务解析：「模型服务」里已连接的平台优先，其次是用户直接填写的兼容接口。"""
    from . import translation_config as module
    return module.profile(host)


def model_name(profile):
    if not profile:
        return ''
    return f"{profile.get('model') or ''} @ {profile.get('base_url') or ''}".strip(' @')


# 配置放在 translation_config 模块里，这里只做转发，避免两处各存一份口径。
def translation_config(host):
    from . import translation_config as module
    return module.config(host)


def public_config(host):
    from . import translation_config as module
    return module.public(host)


def save_config(host, body):
    from . import translation_config as module
    return module.save(host, body)


# 页眉/页脚的文字特征：只用于**界面上的说明**（告诉用户为什么这一段被当成附属信息），
# 不再用它决定"翻不翻"——那只看版面上的位置（见 _segment_kind），规则更简单也更可核对。
_FURNITURE_PATTERNS = [
    re.compile(r'^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$'),                       # 只有页码
    re.compile(r'(?:第\s*)?[0-9０-９一二三四五六七八九十]{1,3}\s*[卷期](?:\s|$)'),   # 第 53 卷 / 第 3 期
    re.compile(r'(?:vol\.?|no\.?|issue)\s*\d', re.I),                        # Vol. 53 / No. 3
    re.compile(r'(?:journal|transactions|proceedings)\s+of\b', re.I),        # 刊名
    re.compile(r'^\s*(?:received|accepted|published)\b.*\d{4}', re.I),       # 收稿/录用日期
    re.compile(r'^\s*(?:doi|https?://|www\.)', re.I),
    re.compile(r'^\s*[ivxlcdm]{1,7}\s*$', re.I),                             # 罗马数字页码
]
FURNITURE_MARGIN = 0.095          # 页眉区：页面顶部 9.5%
NOTE_ZONE = 0.14                  # 页脚注区：页面底部 14%
# 脚注/图表题注的文字特征（与页眉同一原则：位置 + 特征，特征只用来**确认**位置判断）。
_NOTE_PATTERNS = [
    re.compile(r'^\s*(?:\d{1,2}|[*†‡§¶])\s*(?:corresponding|e-?mail|作者|通讯作者)', re.I),
    re.compile(r'^\s*(?:基金|资助|收稿日期|录用日期|作者简介|通信作者)'),
    re.compile(r'^\s*(?:https?://|www\.|doi[:\s])', re.I),
    re.compile(r'^\s*(?:图|表|fig(?:ure)?|table)\s*\.?\s*\d', re.I),
]
SHORT_NOTE_CHARS = 60             # 页脚区里的"短行"上限


def _segment_kind(text, box, page_height, page_font=0, segment_font=0):
    """返回 'body' / 'furniture' / 'note'。

    位置划定范围，特征做确认——**两条都要满足**才判成附属信息：
    - 页眉：落在顶部 9.5% 之内，且是短行、或带页码/卷期/刊名等特征；
    - 注释：落在底部 14% 之内，且是短行、带脚注特征，或字号明显小于正文。

    为什么注释也要"特征确认"（第五次反馈"译文的段落有时候会从中间截断"）：
    长段落会被按 900 字符切成几段，**尾巴那一段的起点经常正好落在页面底部**。
    上一版只看位置，于是这一段正文被当成脚注、默认不翻译，界面上就变成
    "译文翻到一半，后面接的是一段没翻译的原文"——看起来就是从中间截断了。
    现在只有"短行 / 小字号 / 脚注特征"三者之一才当注释，正文尾巴照翻。
    扫描件没有版面框时一律按正文处理（宁可多翻，不可漏翻）。
    """
    stripped = (text or '').strip()
    if not stripped or not box or not page_height:
        return 'body'
    compact = re.sub(r'\s+', ' ', stripped)
    y0, y1 = box[1], box[3]
    if y1 <= FURNITURE_MARGIN:
        # 页眉带放宽到 9.5%，但**只认"看起来就是附属信息"的行**：
        # 短行、或含页码/卷期/刊名等特征。这样正文首行（哪怕落在这一带）不会被误判。
        if len(compact) <= 40 or any(pattern.search(compact) for pattern in _FURNITURE_PATTERNS):
            return 'furniture'
    if y0 >= 1 - NOTE_ZONE:
        smaller = bool(page_font and segment_font and segment_font <= page_font * 0.88)
        if (len(compact) <= SHORT_NOTE_CHARS or smaller
                or any(pattern.search(compact) for pattern in _NOTE_PATTERNS)):
            return 'note'
    return 'body'


def _column_of(box):
    """段落属于哪一栏：full（通栏）/ left / right。没有框时按通栏处理。"""
    if not box:
        return 'full'
    x0, x1 = box[0], box[2]
    if x0 <= 0.12 and x1 >= 0.88:
        return 'full'
    if x1 <= 0.56:
        return 'left'
    if x0 >= 0.44:
        return 'right'
    return 'full'


def decorate_segments(segments, boxes, page_data=None):
    """给每段补上**版面语义**：栏位、是否页眉页脚/注释，以及正文的行高与字号。

    界面据此做三件事：悬停时高亮原文对应区域、把页眉页脚与注释单独摆放（默认不翻译它们）、
    按摘引把批注标在译文上。这些都是纯几何判断，不做语义猜测。
    """
    page_height = (page_data or {}).get('height') or 0
    page_width = (page_data or {}).get('width') or 0
    heights = []
    for row, box in zip(segments, boxes):
        if not box or not page_height:
            continue
        line_height = (box[3] - box[1]) * page_height
        if line_height:
            heights.append(line_height)
    # 正文行高取中位数（页眉页脚与脚注都是少数，中位数落在正文上）。
    body_size = sorted(heights)[len(heights) // 2] if heights else 0
    spans = _page_spans(page_data)
    # 整页字号的代表值：脚注/题注是小字号，但它们是少数，中位数仍落在正文上。
    # 用它来判断"这一段的字号是不是明显更小"（注释的第二个特征，见 _segment_kind）。
    page_font = _median_size(spans, None, page_width, page_height)
    for row, box in zip(segments, boxes):
        row['column'] = _column_of(box)
        # 段落文本两个键都要认：引擎切出来的段落用 ``text``，而存库/接口那一层用 ``source``。
        # （只认 source 就会让"页眉页脚"永远判不出来——实际踩过：整页的 kind 全是 body。）
        row['kind'] = _segment_kind(row.get('source') or row.get('text'), box, page_height, page_font,
                                    _median_size(spans, box, page_width, page_height))
    # 这一页是几栏：左右两栏都至少有 2 段正文才算双栏（界面上用来做提示，不再用它排版）。
    left = sum(1 for row in segments if row.get('kind') == 'body' and row.get('column') == 'left')
    right = sum(1 for row in segments if row.get('kind') == 'body' and row.get('column') == 'right')
    # 原文正文的**字号**（PDF 单位）：取自页面数据里每个字形片的 size，只统计落在正文段里的那些。
    body_font = _body_font_size(page_data, segments, boxes, page_width, page_height)
    return {'body_line_height': round(body_size, 2), 'body_font_size': round(body_font, 2),
            'columns': 2 if left >= 2 and right >= 2 else 1}


def _page_spans(page_data):
    return (page_data or {}).get('spans') or (page_data or {}).get('characters') or []


def _median_size(spans, box, page_width, page_height):
    """字形片字号的中位数；给了 box 就只统计与它相交的那些。

    字形片的坐标是页面单位，而段落的框是归一化坐标（见 ``citation.locate_page_text``），
    所以这里先按页宽页高归一化再判断相交。
    """
    if not spans or not page_width or not page_height:
        return 0
    sizes = []
    for span in spans:
        glyph = span.get('bbox') or []
        size = span.get('size')
        if len(glyph) != 4 or not isinstance(size, (int, float)) or isinstance(size, bool) or size <= 0:
            continue
        try:
            left, top = float(glyph[0]) / page_width, float(glyph[1]) / page_height
            right, bottom = float(glyph[2]) / page_width, float(glyph[3]) / page_height
        except (TypeError, ValueError, ZeroDivisionError):
            continue
        if box and not (right > box[0] and left < box[2] and bottom > box[1] and top < box[3]):
            continue
        sizes.append(float(size))
    if not sizes:
        return 0
    sizes.sort()
    return sizes[len(sizes) // 2]


def _body_font_size(page_data, segments, boxes, page_width, page_height):
    """正文的字号中位数（PDF 单位）；取不到就返回 0。"""
    sizes = []
    for row, box in zip(segments, boxes):
        if row.get('kind') != 'body' or not box:
            continue
        size = _median_size(_page_spans(page_data), box, page_width, page_height)
        if size:
            sizes.append(size)
    if not sizes:
        return 0
    sizes.sort()
    return sizes[len(sizes) // 2]


def _translate_one(segment, config, profile):
    from . import translation_engine as engine
    output = engine.remote_translate([segment], profile, config['target_language'])
    return {'text': output[segment['index']], 'terms': [], 'unknown_terms': [], 'coverage': None,
            'engine': REMOTE_ENGINE, 'service': model_name(profile)}


def translate_page(host, doc_id, page, force=False, only=None, progress=None, include_furniture=False):
    """翻译（或复用）一页。返回这一页的全部段落，含复用/过期/未翻译的实际状态。"""
    from . import translation_engine as engine
    meta = host.document(doc_id)
    if not 1 <= page <= meta['pages']:
        raise host.HTTPException(400, '页码超出范围')
    path = host.document_path(meta)
    text, origin = read_text(host, doc_id, page, meta, path)
    pieces = engine.split_segments(text)
    if not pieces:
        raise host.HTTPException(422, '这一页没有可翻译的文字：扫描页请先识别，或先用框选识别取文字。')
    config = translation_config(host)
    profile = engine_profile(host)
    if not (profile and profile.get('model') and profile.get('base_url')):
        raise host.HTTPException(409, '还没有可用的翻译服务：请在「模型服务」里选择一个已连接的模型，'
                                      '或在「设置 · 翻译」里直接填写一个兼容接口的地址与模型。')
    # 复用还要求**服务一致**：换服务后旧译文默认保留，只有"重新翻译（覆盖）"才覆盖，
    # 否则用户换了更好的服务却一直看到旧引擎的译文，还以为设置没生效。
    service_label = model_name(profile)
    stored = {row['segment']: row for row in stored_segments(host, doc_id, page)}
    wanted = None if only is None else set(int(value) for value in only)
    # 版面框同时给三件事用：界面上的悬停高亮、按原文分栏摆放、以及"页眉页脚/注释"的判定。
    boxes = page_boxes(host, doc_id, page, [piece['text'] for piece in pieces], meta, path)
    page_data = host.page_result(doc_id, page) or {}
    layout = decorate_segments(pieces, [box['bbox'] for box in boxes], page_data)
    missing = []
    for piece in pieces:
        index = piece['index']
        if wanted is not None and index not in wanted:
            continue
        pieces[index]['source_hash'] = engine.source_hash(piece['text'])
        if piece.get('kind') in ('furniture', 'note') and not include_furniture:
            # 页眉页脚与注释**默认不翻译**：用户明确反馈过它们被混进正文。
            # 界面会按原位显示原文并标注原因，所以这不是"丢内容"。
            # 但用户**点名**要翻这一段（界面上的"翻译这一段"）时照翻不误。
            if wanted is None or index not in wanted:
                continue
        current = stored.get(index)
        reusable = (current and not force and current['source_hash'] == pieces[index]['source_hash']
                    and current['service'] == service_label)
        if reusable:
            continue
        missing.append({'index': index, 'text': piece['text']})
    done = 0
    failure = ''
    if missing:
        # **并发**发这一段里缺的段：串行等十几次往返就是用户说的"整页翻译非常慢"。
        # 用 batch 版是为了保住原来的失败语义——某一段失败时，已经翻好的段照样落库。
        output, errors = engine.remote_translate_batch(
            missing, profile, config['target_language'], check=_cancelled, progress=progress)
        if errors:
            if _cancelled():
                raise host.HTTPException(499, '已停止本次翻译') from None
            # 单段失败不能让整页以 500 收场：把可读原因写进这一页的状态，界面照实显示。
            failure = str(errors[0]) or '翻译失败'
        for piece in missing:
            text = output.get(piece['index'])
            if not text:
                continue
            values = {'text': text, 'terms': [], 'unknown_terms': [], 'coverage': None,
                      'engine': REMOTE_ENGINE, 'service': service_label}
            _upsert(host, doc_id, page, piece['index'], values, piece['text'], engine.source_hash(piece['text']),
                    values['engine'], values['service'])
            done += 1
    stored = {row['segment']: row for row in stored_segments(host, doc_id, page)}
    # 版面框（boxes）在上面的分类里已经算过一次了，这里**不重算**：页面与文本都没变，
    # 重算只是白白再解析一遍页面数据——整页翻译本来就要等十几次请求，没必要再等这个。
    rows = []
    for piece, box in zip(pieces, boxes):
        index = piece['index']
        current = stored.get(index)
        hash_value = engine.source_hash(piece['text'])
        if current:
            state = 'edited' if current['edited'] else ('stale' if current['source_hash'] != hash_value else 'reused')
            # 上一版没有"服务"这个概念，旧记录的 service 可能是"内置术语翻译"或空：
            # 那些不是翻译，界面要能认出来并提示用当前服务重译。
            legacy = not current['service'] or '术语' in (current['service'] or '')
        else:
            state = 'missing'
            legacy = False
        rows.append({'index': index, 'source': piece['text'], 'source_hash': hash_value,
                     'id': current['id'] if current else '',
                     'text': current['text'] if current else '', 'state': state,
                     'engine': current['engine'] if current else '', 'service': current['service'] if current else '',
                     'edited': bool(current['edited']) if current else False,
                     'legacy': legacy,
                     'kind': piece.get('kind') or 'body',
                     'column': piece.get('column') or 'full',
                     # 超长段落被切开时，切出来的几段带同一个 para：界面把它们显示成一个自然段。
                     'para': piece.get('para') or 0,
                     'bbox': box['bbox'], 'boxes': box['boxes'], 'located': box['located'],
                     # 译文看起来没翻完（服务端默认输出上限导致的静默截断）：界面会提示重译这一段。
                     'truncated': bool(current and current['text']
                                       and engine.looks_unfinished(piece['text'], current['text'])),
                     'updated': current['updated'] if current else ''})
    covered = sum(1 for row in rows if row['text'] and row['state'] != 'stale')
    status = 'partial' if failure and covered else 'failed' if failure else 'done' if covered == len(rows) else 'partial'
    state = _set_state(host, doc_id, page, source_hash=engine.source_hash(text), engine=REMOTE_ENGINE,
                       status=status, error=failure, done=covered, total=len(rows))
    return {'page': page, 'origin': origin, 'segments': rows,
            'service': service_label,
            'legacy': sum(1 for row in rows if row['legacy']),
            'furniture': sum(1 for row in rows if row['kind'] != 'body'),
            'page_size': {'width': page_data.get('width'), 'height': page_data.get('height')},
            # 版面参数：界面按"同样的分栏数 + 与原文一致的字号"排译文（见 static/translation.js）。
            'columns': layout['columns'], 'body_line_height': layout['body_line_height'],
            'body_font_size': layout['body_font_size'],
            'target_language': config['target_language'],
            'translated': covered, 'total': len(rows), 'status': status, 'error': failure,
            'translated_at': state.get('updated', '')}


def translate_segment(host, doc_id, page, index):
    """只重译指定的一段（需求：允许对指定内容重新翻译）。"""
    result = translate_page(host, doc_id, page, force=True, only=[index])
    row = next((item for item in result['segments'] if item['index'] == index), None)
    if row is None:
        raise host.HTTPException(404, '这一段在当前页里不存在（原文可能已改变），请刷新后重试')
    return {'segment': row, 'status': result['status'], 'error': result['error']}


def edit_segment(host, doc_id, text):
    """人工修改译文。改过的段落标记为 edited，原机器译文留在 previous_text 里可恢复。"""
    with _host_db(host) as c:
        row = c.execute('SELECT * FROM translation_segments WHERE id=? AND document_id=?', (text.id, doc_id)).fetchone()
        if not row:
            raise host.HTTPException(404, '译文不存在')
        if row['origin'] == 'machine':
            c.execute("""UPDATE translation_segments SET text=?,edited=1,origin='user',
                         previous_text=text,previous_origin='machine',updated=CURRENT_TIMESTAMP WHERE id=?""",
                      (text.text, text.id))
        else:
            c.execute("UPDATE translation_segments SET text=?,edited=1,origin='user',updated=CURRENT_TIMESTAMP WHERE id=?",
                      (text.text, text.id))
    return {'ok': True, 'id': text.id}


def restore_segment(host, doc_id, segment_id):
    """撤销人工修改：回到上一次的机器译文；没有可恢复的版本就如实说明，而不是假装恢复。"""
    with _host_db(host) as c:
        row = c.execute('SELECT * FROM translation_segments WHERE id=? AND document_id=?', (segment_id, doc_id)).fetchone()
        if not row:
            raise host.HTTPException(404, '译文不存在')
        if not row['edited']:
            return {'ok': True, 'restored': False, 'message': '这段本来就是机器译文，没有需要撤销的修改。'}
        if not row['previous_text']:
            # 没有可恢复的机器译文（旧版本数据，或当初只存了手改内容）：不编造内容。
            c.execute("UPDATE translation_segments SET edited=0,updated=CURRENT_TIMESTAMP WHERE id=?", (segment_id,))
            return {'ok': True, 'restored': False,
                    'message': '这段没有保存下来的机器译文：可以点「重新翻译」用当前服务重译这一段。'}
        c.execute("""UPDATE translation_segments SET text=previous_text,edited=0,origin=previous_origin,
                     previous_text='',previous_origin='',updated=CURRENT_TIMESTAMP WHERE id=?""", (segment_id,))
    return {'ok': True, 'restored': True}


def document_progress(host, doc_id):
    meta = host.document(doc_id)
    states = {row['page']: row for row in page_states(host, doc_id)}
    with _host_db(host) as c:
        counts = {row['page']: row['n'] for row in c.execute(
            'SELECT page,COUNT(*) AS n FROM translation_segments WHERE document_id=? GROUP BY page', (doc_id,))}
    translated = sum(1 for page in counts if states.get(page, {}).get('status') in ('done', 'partial'))
    job = _JOBS.get(doc_id) or {}
    return {'document_id': doc_id, 'pages': meta['pages'], 'translated_pages': translated,
            'segments': sum(counts.values()), 'pages_detail': states,
            'job': {'running': bool(job.get('running')), 'page': job.get('page', 0), 'pages': job.get('pages', 0),
                    'error': job.get('error', ''), 'engine': job.get('engine', ''), 'started': job.get('started', 0)}}


def _cancelled():
    try:
        from . import cancellation
        cancellation.check()
        return False
    except Exception:
        return True


def stop_document(host, doc_id):
    with _JOBS_LOCK:
        job = _JOBS.get(doc_id)
        if not job or not job.get('running'):
            return {'ok': True, 'running': False, 'message': '这篇文献当前没有正在进行的翻译。'}
        job['stop'] = True
        request_id = job.get('request_id')
    if request_id:
        try:
            from . import cancellation
            cancellation.cancel(request_id)
        except Exception:
            pass
    return {'ok': True, 'running': False, 'message': '已请求停止：当前段落完成后结束。'}


def _page_list(total, pages=None):
    if not pages:
        return list(range(1, total + 1))
    wanted = sorted({int(page) for page in pages if 1 <= int(page) <= total})
    return wanted


def start_document(host, doc_id, pages=None, force=False, request_id=''):
    """整篇后台翻译：一个文献同时只有一个任务，进度写库，可随时停止。"""
    meta = host.document(doc_id)
    host.document_path(meta)
    with _JOBS_LOCK:
        existing = _JOBS.get(doc_id)
        if existing and existing.get('running'):
            return {'started': False, 'running': True, 'message': '这篇文献已经在翻译中。', **document_progress(host, doc_id)}
        plan = _page_list(meta['pages'], pages)
        job = {'running': True, 'stop': False, 'page': 0, 'pages': len(plan), 'error': '',
               'service': model_name(engine_profile(host)),
               'started': time.time(), 'request_id': request_id}
        _JOBS[doc_id] = job
    thread = threading.Thread(target=_worker, args=(host, doc_id, plan, force, job), name=f'translate-{doc_id[:8]}', daemon=True)
    thread.start()
    return {'started': True, 'running': True, 'pages': len(plan),
            'message': f'已在后台开始翻译 {len(plan)} 页，可继续阅读。'}


def _worker(host, doc_id, plan, force, job):
    from . import cancellation
    token = None
    try:
        if job.get('request_id'):
            token = cancellation.begin_request(job['request_id'])
        for page in plan:
            if job.get('stop'):
                break
            job['page'] = page
            try:
                translate_page(host, doc_id, page, force=force,
                               progress=lambda done, total, p=page: _job_progress(job, p, done, total))
            except Exception as exc:
                if job.get('stop') or _cancelled():
                    break
                job['error'] = f'第 {page} 页：{getattr(exc, "detail", None) or exc}'
                break
            time.sleep(0.01)
    finally:
        if token is not None:
            cancellation.end_request(token)
        with _JOBS_LOCK:
            job['running'] = False
        with _host_db(host) as c:
            c.execute("INSERT OR REPLACE INTO model_settings VALUES('translation_last_run',?)",
                      (json.dumps({'document_id': doc_id, 'pages': len(plan), 'error': job.get('error', ''),
                                   'finished': time.time()}, ensure_ascii=False),))
        _JOBS.pop(doc_id, None)


def _job_progress(job, page, done, total):
    job['page'] = page
    job['done'] = done
    job['total'] = total


def public_state(host, doc_id):
    state = document_progress(host, doc_id)
    job = state['job']
    if job['running'] and job.get('page'):
        page = page_state(host, doc_id, job['page']) or {}
        job['done'] = page.get('done', 0)
        job['total'] = page.get('total', 0)
    return state


def annotations_with_translation(host, doc_id, annotations):
    """给批注附上它所在页、与摘引重叠的译文（需求"额外处理 1"）。"""
    from . import translation_engine as engine
    by_page = {}
    for annotation in annotations:
        page = int(annotation.get('page') or 0)
        if page < 1:
            continue
        if page not in by_page:
            by_page[page] = {row['segment']: row for row in stored_segments(host, doc_id, page)}
        rows = by_page[page]
        quote = engine.normalize(annotation.get('quote') or '')
        matched = []
        if quote:
            for row in sorted(rows.values(), key=lambda item: item['segment']):
                source = engine.normalize(row.get('source') or '')
                if not source or not row.get('text'):
                    continue
                if source in quote or quote in source:
                    matched.append(row)
        items = []
        for row in matched:
            # 摘引只是整段里的一句时，译文也只给对应的那一句——用户反馈过"即使只标识了所选的
            # 几个文本，内容依旧是一整段"。对不上就退回整段译文（宁可多给，不可给错）。
            text = row['text']
            narrowed = False
            if quote and quote != engine.normalize(row['source']):
                piece, _exact = engine.map_sentences(row['source'], row['text'], annotation.get('quote') or '')
                if piece and piece.strip() and piece.strip() != row['text'].strip():
                    text, narrowed = piece.strip(), True
            # ``excerpt`` 给界面用：在译文上**只标这一小段**，而不是整段。
            # 从译文栏加的批注带着用户真正选中的那几个字（最准）；在原文页上选的批注没有这个字段，
            # 这里就按句级对齐把摘引映射成译文里的一段——否则用户会看到"原文标的是所选文字、
            # 译文却整段都标上了"（用户第十一次反馈就是这个）。
            excerpt = (annotation.get('translation_excerpt') or '').strip()
            if not excerpt:
                excerpt = text if narrowed else ''
            items.append({'segment': row['segment'], 'source': row['source'], 'text': text,
                          'full_text': row['text'] if narrowed else '',
                          'narrowed': narrowed, 'excerpt': excerpt,
                          'edited': bool(row['edited']), 'engine': row['engine']})
        annotation['translation'] = items
        annotation['translation_engine'] = matched[0]['engine'] if matched else ''
    return annotations


def search_chunks(host, document_ids):
    """把译文当作独立的检索来源返回（需求"额外处理 3"）。

    刻意不写入 ``chunks`` 表：那张表是原文索引，引用核验、阅读与批注都依赖它只含原文。
    这里返回的是带 ``translation`` 标记的派生行，检索命中后界面会注明"这是译文"。
    """
    from .literature_core.structure import text_quality
    ids = [doc_id for doc_id in dict.fromkeys(document_ids) if doc_id]
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    with _host_db(host) as c:
        rows = [dict(row) for row in c.execute(
            f'SELECT * FROM translation_segments WHERE document_id IN ({marks}) AND text<>\'\' ORDER BY document_id,page,segment', ids)]
    result = []
    for row in rows:
        quality = text_quality(row['text'])
        if quality['suspect']:
            continue
        result.append({'id': f"T:{row['id']}", 'document_id': row['document_id'], 'page': row['page'],
                       'text': row['text'], 'bbox': None, 'bbox_space': 'translation',
                       'source_text': row['source'], 'translation': True,
                       'engine': row['engine'], 'edited': bool(row['edited']),
                       'segment': row['segment'], 'translation_id': row['id']})
    return result


def anchor_selection(host, doc_id, page, excerpt, segment=None):
    """把"译文里选中的一小段"映射回**原文的一段**（句级对齐），并给出它在页面上的位置。

    用户反馈："批注位置仍旧不准确，一方面会划到不属于的位置，另一方面即使只标识了所选的几个
    文本，内容依旧是一整段。" 之前只把摘引取成整段原文，于是原文页上的高亮是整段、批注栏里
    的"原文所引"和译文也是整段。现在：先在选中的译文里定出它覆盖了哪几句（:func:`sentence_span`），
    再按句级对齐映射到原文的对应句子（:func:`map_sentences`），最后用既有的引用定位把这几句
    在原页上的框算出来——因此高亮只盖住真正相关的那几行。

    对不上（例如用户抄了一段、或这一页还没译文）就返回该段整段原文，并如实标 ``exact=False``。
    """
    from . import translation_engine as engine
    text = (excerpt or '').strip()
    if not text:
        return {'quote': '', 'bbox': None, 'exact': False, 'matched': False, 'translation_span': None}
    rows = stored_segments(host, doc_id, page)
    target = engine.normalize(text)
    candidates = []
    for row in rows:
        source = row.get('source') or ''
        translated = row.get('text') or ''
        if not source or not translated:
            continue
        if segment is not None and int(row['segment']) != int(segment):
            continue
        if segment is None and engine.normalize(translated).find(target) < 0:
            continue
        candidates.append(row)
    if not candidates:
        return {'quote': text, 'bbox': None, 'exact': False, 'matched': False, 'translation_span': None}
    rows = sorted(candidates, key=lambda item: abs(len(item.get('text') or '') - len(text)))
    row = rows[0]
    quote, exact = engine.map_sentences(row.get('text') or '', row.get('source') or '', text)
    quote = quote or row['source']
    meta = host.document(doc_id)
    path = host.document_path(meta)
    boxes = page_boxes(host, doc_id, page, [quote], meta, path)
    box = boxes[0] if boxes else {'bbox': None, 'located': False}
    fallback = False
    if not box.get('bbox') and quote != (row.get('source') or ''):
        # 那几句在原页上定位不到（扫描页、或者校对过的文字对不上行框）时，退一步用**整段**的框。
        # 这一步很重要：没有框的批注会被存成"整页批注"——用户报过"浮条的高亮/下划线直接就是
        # 整页批注了"，宁可框大一点（至少是这一段），也不要整页。
        wider = page_boxes(host, doc_id, page, [row.get('source') or ''], meta, path)
        if wider and wider[0].get('bbox'):
            box, fallback = wider[0], True
    return {'quote': quote, 'bbox': box.get('bbox'), 'located': bool(box.get('located')),
            'exact': bool(exact), 'matched': True, 'fallback': fallback, 'segment': row['segment'],
            'source': row.get('source') or '', 'translation': row.get('text') or ''}


def lookup_by_quote(host, doc_id, page, quote):
    """给"只显示译文时也能对着译文批注"用：按译文原文反查它属于哪一段。"""
    from . import translation_engine as engine
    target = engine.normalize(quote)
    hits = []
    for row in stored_segments(host, doc_id, page):
        source = engine.normalize(row.get('source') or '')
        text = engine.normalize(row.get('text') or '')
        if not source:
            continue
        if source in target or target in source or (len(target) >= 6 and target in text):
            hits.append({'segment': row['segment'], 'source': row['source'], 'text': row['text'],
                         'match': 'source' if source in target or target in source else 'translation'})
    return hits
