"""每篇文献的「无关区域」屏蔽规则：页眉、页脚、页码、水印、侧栏等排版噪声。

设计口径（这些是改代码时最容易被破坏的前提）：

- 规则**只存在本机**（reader.sqlite3 的 page_masks 表），**从不改写用户的 PDF 字节**。
  产品承诺原文不被改写，所以"屏蔽"只能是读取时的过滤，不是删除：删掉规则就恢复原样。
- 坐标一律 0..1 归一化。同一页会以不同尺寸渲染（阅读区、缩略图、后台索引），
  只有归一化坐标才能在这三处通用。
- ``page=0`` 表示"所有页"（用户在一页上画、选择应用于全部页）；正数是"只作用于该页"。
  两种规则要能同时存在：全局规则定义版式噪声，单页规则处理某一页独有的水印/侧栏。
- 同一文献、同一页、同一范围重复添加要**幂等**：存两份不会更正确，只会让
  "管理规则"里出现重复项、用户删了还能看到一条。
- 过滤按**相交**判断：只要 span 的框与遮罩矩形有任何重叠就整行丢弃。按包含判断会
  漏掉"页眉行比选的框长一点"的情况，那一截文字仍然可选、可检索，等于没屏蔽。
- 规则可审计：id、页码、坐标、创建时间都留在表里，列表与删除都按 document_id 校验。
"""
import hashlib
import math
import sqlite3
import threading
import time
import uuid

# 单篇文献的规则条数上限。不是为了限制正常使用（页眉页脚最多几条），
# 而是防止循环脚本把"管理规则"列表和每次翻页的过滤都拖垮。
MAX_MASKS = 200

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS page_masks(
  id TEXT PRIMARY KEY,
  document_id TEXT NOT NULL,
  page INTEGER NOT NULL DEFAULT 0,
  x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
  created TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS page_mask_document ON page_masks(document_id, page);
"""

# 索引重建状态只放进程内：它是"这一次后台重建进行到哪"的运行态，
# 不是需要跨重启保留的数据（重启后规则仍在，重建会在下次提问/下一次改动时重做）。
_STATUS = {}
_PENDING = {}
_STATUS_LOCK = threading.RLock()


def connect():
    """复用 reader.sqlite3：规则与文献、chunks 在同一份本地数据库里，备份/迁移口径一致。"""
    from .settings import storage
    connection = storage()
    connection.row_factory = sqlite3.Row
    ensure_tables(connection)
    return connection


def ensure_tables(connection):
    connection.executescript(TABLE_SQL)


def _validate(page, x0, y0, x1, y1):
    try:
        page = int(page)
    except (TypeError, ValueError):
        raise ValueError('页码必须是整数') from None
    if page < 0:
        raise ValueError('页码不能为负数')
    numbers = []
    for name, value in (('x0', x0), ('y0', y0), ('x1', x1), ('y1', y1)):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError(f'坐标 {name} 不是数字') from None
        if not math.isfinite(number):
            raise ValueError(f'坐标 {name} 不是有限数值')
        numbers.append(round(number, 6))
    x0, y0, x1, y1 = numbers
    if not 0 <= x0 < x1 <= 1 or not 0 <= y0 < y1 <= 1:
        raise ValueError('区域坐标超出页面范围：需要 0 ≤ x0 < x1 ≤ 1、0 ≤ y0 < y1 ≤ 1')
    return page, x0, y0, x1, y1


def _row(row):
    return {'id': row['id'], 'document_id': row['document_id'], 'page': int(row['page']),
            'x0': float(row['x0']), 'y0': float(row['y0']),
            'x1': float(row['x1']), 'y1': float(row['y1']), 'created': row['created']}


def _rows(document_id):
    with connect() as connection:
        return [row for row in connection.execute(
            'SELECT * FROM page_masks WHERE document_id=? ORDER BY page, created, rowid', (document_id,))]


def list_masks(document_id):
    """该文献的全部规则（含"所有页"与单页），按页码稳定排序。"""
    return [_row(row) for row in _rows(document_id)]


# 任务书把"列出规则"命名为 list()：保留一个显式别名，接口与后续调用方不必各自发明名字。
# 模块内部不使用内置 list()，因此这里的遮蔽不会影响本文件其它函数。
list = list_masks


def add(document_id, page, x0, y0, x1, y1):
    """新增一条规则；返回 ``(mask, created)``。

    幂等：同一文献、同一页、同一坐标（1e-6 内视为相同）已存在时直接返回原规则，
    ``created`` 为 False，调用方据此决定要不要真的重建索引。
    """
    page, x0, y0, x1, y1 = _validate(page, x0, y0, x1, y1)
    with connect() as connection:
        existing = connection.execute(
            'SELECT * FROM page_masks WHERE document_id=? AND page=? AND '
            'ABS(x0-?)<1e-6 AND ABS(y0-?)<1e-6 AND ABS(x1-?)<1e-6 AND ABS(y1-?)<1e-6',
            (document_id, page, x0, y0, x1, y1)).fetchone()
        if existing:
            return _row(existing), False
        count = connection.execute('SELECT COUNT(*) FROM page_masks WHERE document_id=?',
                                   (document_id,)).fetchone()[0]
        if count >= MAX_MASKS:
            raise ValueError(f'这篇文献的屏蔽规则已达上限（{MAX_MASKS} 条），请先删除不再需要的规则')
        mask_id = uuid.uuid4().hex
        connection.execute(
            'INSERT INTO page_masks(id,document_id,page,x0,y0,x1,y1) VALUES(?,?,?,?,?,?,?)',
            (mask_id, document_id, page, x0, y0, x1, y1))
        connection.commit()
        return _row(connection.execute('SELECT * FROM page_masks WHERE id=?', (mask_id,)).fetchone()), True


def remove(document_id, mask_id):
    """删除一条规则；不存在（或不属于这篇文献）时返回 None，由接口回 404。"""
    with connect() as connection:
        row = connection.execute('SELECT * FROM page_masks WHERE document_id=? AND id=?',
                                 (document_id, str(mask_id))).fetchone()
        if not row:
            return None
        connection.execute('DELETE FROM page_masks WHERE id=?', (row['id'],))
        connection.commit()
        return _row(row)


def purge_document(document_id):
    """彻底删除文献时清掉它的规则（调用方负责；规则本身不含正文，只是不该留孤儿行）。"""
    with connect() as connection:
        connection.execute('DELETE FROM page_masks WHERE document_id=?', (document_id,))
        connection.commit()


def _regions(rows, page):
    return tuple((float(row['x0']), float(row['y0']), float(row['x1']), float(row['y1']))
                 for row in rows if int(row['page']) in (0, int(page)))


def all_regions(document_id):
    """整篇的规则，形如 ``((page,x0,y0,x1,y1),...)``；元组可直接做 lru_cache 的键。

    页码 0 表示所有页，交给 ``split_regions`` 按页展开。
    """
    return tuple((int(row['page']), float(row['x0']), float(row['y0']),
                  float(row['x1']), float(row['y1'])) for row in _rows(document_id))


def split_regions(regions, page):
    """从整篇规则里取出该页生效的 4 元组；``page=0`` 的规则对所有页生效。"""
    if not regions:
        return ()
    number = int(page)
    return tuple((x0, y0, x1, y1) for mask_page, x0, y0, x1, y1 in regions
                 if mask_page == 0 or mask_page == number)


def regions(document_id, page):
    """该页生效的遮罩矩形（4 元组序列），供 reading_adapter / 页面接口过滤使用。"""
    return _regions(_rows(document_id), page)


def for_page(document_id, page):
    """页面接口一次取齐：该页生效的遮罩、该页应画的规则、规则版本号。

    放在一次连接里做完，是因为页面接口每翻一页都会调用它；
    分三次查库会让翻页多付两倍的 SQLite 打开/关闭开销。
    """
    rows = _rows(document_id)
    applicable = [_row(row) for row in rows if int(row['page']) in (0, int(page))]
    return {'regions': _regions(rows, page), 'masks': applicable,
            'revision': _revision(rows)}


def _revision(rows):
    if not rows:
        return ''
    digest = hashlib.sha256()
    for row in rows:
        digest.update(f"{row['id']}|{row['page']}|{row['x0']}|{row['y0']}|{row['x1']}|{row['y1']}\n".encode('utf-8'))
    return digest.hexdigest()[:16]


def revision(document_id):
    """规则版本号：规则一变它就变，缓存键可以据此区分"遮罩前/后"。"""
    return _revision(_rows(document_id))


def _box_of(item):
    if isinstance(item, dict):
        return item.get('bbox')
    if isinstance(item, (list, tuple)) and len(item) == 4:
        return item
    return None


def _overlaps(box, regions):
    """``box`` 是归一化 0..1 的 [x0,y0,x1,y1]；与任一遮罩矩形有重叠即 True。"""
    if not box or len(box) != 4:
        return False
    try:
        x0, y0, x1, y1 = (float(value) for value in box)
    except (TypeError, ValueError):
        return False
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return any(x0 < right and x1 > left and y0 < bottom and y1 > top
               for left, top, right, bottom in regions)


def filter_page_data(data, regions):
    """按遮罩过滤 ``pdf_engine.page_data`` 的 spans / characters / text。

    坐标是页面像素（左上原点，已按视觉方向变换），这里用 width/height 归一化后判断。
    返回新字典，**不改动传入的字典**——它可能正是 lru_cache 里的共享对象。
    """
    if not regions:
        return data
    width = float(data.get('width') or 0)
    height = float(data.get('height') or 0)
    if width <= 0 or height <= 0:
        return data

    def masked(box):
        if not box or len(box) != 4:
            return False
        bounds = (box[0] / width, box[1] / height, box[2] / width, box[3] / height)
        return _overlaps(bounds, regions)

    spans = [span for span in data.get('spans') or [] if not masked(span.get('bbox'))]
    characters = [glyph for glyph in data.get('characters') or [] if not masked(glyph.get('bbox'))]
    # 页面文字层与"阅读原文"都读 spans：遮罩后 text 也必须只含保留的行，
    # 否则翻译、视觉解读等下游仍会拿到被屏蔽的页眉文字。
    text = '\n'.join(span.get('text', '') for span in spans if span.get('text', '').strip())
    return {**data, 'spans': spans, 'characters': characters, 'text': text}


def filter_boxes(lines, regions):
    """过滤 OCR 行框列表（bbox 已是 0..1 页面比例）。批量识别/引用定位共用。"""
    if not regions:
        return [line for line in lines or []]
    return [line for line in (lines or []) if not _overlaps(_box_of(line), regions)]


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _status_copy(document_id):
    with _STATUS_LOCK:
        current = dict(_STATUS.get(document_id) or {})
    current.setdefault('state', 'idle')
    current.setdefault('total', 0)
    current.setdefault('done', 0)
    current.setdefault('error', '')
    current.setdefault('updated', '')
    current.setdefault('pages', [])
    return current


def index_status(document_id):
    """检索索引重建状态 + 当前规则版本号（前端据此如实显示"正在重建索引"）。"""
    status = _status_copy(document_id)
    status['revision'] = revision(document_id)
    return status


def affected_pages(page, total):
    """一条 ``page=0`` 规则影响全部页；单页规则只影响该页。"""
    number = int(page)
    if number == 0:
        return [page for page in range(1, int(total) + 1)]
    return [number] if 1 <= number <= int(total) else []


def _rebuild_pages(host, document_id, pages, status):
    """删除受影响页由**文本层 span** 生成的 chunks，再用遮罩后的 spans 重新插入。

    只删 bbox 形如 ``[...]`` 的行：那是上传/Zotero 按字形写入的片段。
    ``bbox='null'`` 的行来自身份不同的来源（整页 OCR 或用户校订产生的平铺文本、
    远程识别的区域文本），它们没有字形坐标，删掉就再也恢复不了，因此保留。
    """
    meta = host.document(document_id)
    path = host.document_path(meta)
    masks = all_regions(document_id)
    total = len(pages)
    done = 0
    # 一次打开整本 PDF 会长时间占住 pdf_engine 的全局锁，翻页/缩略图都会卡住。
    # 分批打开（每批 20 页）把锁的占用切成小段，后台重建期间界面仍然可用。
    for start in range(0, total, 20):
        batch = pages[start:start + 20]
        collected = {}
        with host.pdf_engine.open_pdf(path) as pdf:
            for number in batch:
                item = pdf[number - 1]
                try:
                    data = filter_page_data(host.pdf_engine.page_data(item), split_regions(masks, number))
                finally:
                    item.close()
                rows = []
                for span in data.get('spans') or []:
                    text = span.get('text') or ''
                    for offset in range(0, len(text), 1000):
                        # 与 library_feature.upload / zotero_feature 的写入口径保持一致：
                        # 同一段文字在"重新导入"与"加/删遮罩后重建"里都切成同样的行，
                        # 检索结果不会因为走了哪条路径而不同。
                        rows.append((uuid.uuid4().hex, document_id, number,
                                     text[offset:offset + 1200], host.json.dumps(span['bbox']), 'visual'))
                collected[number] = rows
        with host.db() as connection:
            for number in batch:
                connection.execute(
                    "DELETE FROM chunks WHERE document_id=? AND page=? AND bbox LIKE '[%'",
                    (document_id, number))
                if collected[number]:
                    connection.executemany(
                        'INSERT INTO chunks(id,document_id,page,text,bbox,bbox_space) VALUES(?,?,?,?,?,?)',
                        collected[number])
        done += len(batch)
        with _STATUS_LOCK:
            status['done'] = done
            status['updated'] = _now()


def _finish(document_id, status, state, error=''):
    with _STATUS_LOCK:
        status.update(state=state, error=error, updated=_now())
        _STATUS[document_id] = status


def _worker(host, document_id, pages):
    """后台重建：一次处理完，期间同一文献后续请求的页面并入下一轮，不丢。"""
    while True:
        status = _status_copy(document_id)
        status.update(state='running', total=len(pages), done=0, error='', pages=pages, updated=_now())
        with _STATUS_LOCK:
            _STATUS[document_id] = status
        try:
            _rebuild_pages(host, document_id, pages, status)
        except Exception as exc:  # 后台线程必须把失败写进状态，不能静默吞掉
            _finish(document_id, status, 'failed', str(exc)[:300])
        else:
            _finish(document_id, status, 'done')
        with _STATUS_LOCK:
            pending = _PENDING.pop(document_id, None)
        if not pending:
            return
        pages = sorted(pending)


def rebuild(host, document_id, pages):
    """规则变化后重建受影响页的检索索引，并返回最新状态。

    ≤40 页的文献**同步**重建：用户改完立刻能用，不必等后台，也不必假装已经生效。
    页数更多的放后台线程，接口返回 "running"，前端如实显示"正在重建索引"。
    """
    meta = host.document(document_id)
    total = int(meta['pages'])
    pages = sorted({int(page) for page in pages if 1 <= int(page) <= total})
    if not pages:
        return index_status(document_id)
    if total <= 40:
        status = {'state': 'running', 'total': len(pages), 'done': 0, 'error': '',
                  'pages': pages, 'updated': _now()}
        with _STATUS_LOCK:
            _STATUS[document_id] = status
        try:
            _rebuild_pages(host, document_id, pages, status)
        except Exception as exc:
            _finish(document_id, status, 'failed', str(exc)[:300])
        else:
            _finish(document_id, status, 'done')
        return index_status(document_id)
    with _STATUS_LOCK:
        current = _STATUS.get(document_id) or {}
        if current.get('state') == 'running':
            # 上一轮还在跑：把这一轮要重建的页并进去，由 worker 循环接续，
            # 而不是起第二个线程同时写同一批 chunks。
            _PENDING.setdefault(document_id, set()).update(pages)
            return index_status(document_id)
        _STATUS[document_id] = {'state': 'running', 'total': len(pages), 'done': 0,
                                'error': '', 'pages': pages, 'updated': _now()}
    threading.Thread(target=_worker, args=(host, document_id, pages),
                     daemon=True, name='page-mask-index').start()
    return index_status(document_id)
