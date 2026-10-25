"""文档导航层最小试点：页 → 标签的"该读哪几页"索引。

设计口径（改代码前先读；这几条是产品承诺，不是实现细节）：

- **只做导航，不做内容**：这张表只用来告诉规划器"这个问题可能落在哪几页"。
  它永远不能进入回答、证据或核验结果——标签是派生数据，不是原文，答话必须回到原文。
- **这一版不调用模型**：标签来自 PDF 自带书签 / 标题规则的章节树，或页面首行（本机规则）。
  因此没有 token 成本，也没有"模型摘要混进证据"的风险；模型生成的 hook 留到第二阶段，
  由 ``build`` 的调用方决定是否接入。
- **必须锚定**：每条节点都带页码；整张表用 ``source_hash(文件指纹, 遮罩版本)`` 锚定。
  文件被替换（重新导入/重连）或无关区域规则变化后，旧表**立即过期**，界面与规划都
  不会把它当最新结果用——宁可回退逐页读取，也不拿过期索引去猜范围。
- **默认关闭**：``doc_navigation_state.enabled`` 初始为 0。用户不开就完全走既有路径，
  行为与改造前一致（``planning_context`` 里只多一个 ``enabled=False`` 的说明字段）。
- **可撤销**：只存在本机 reader.sqlite3 的这两张表里，删除即恢复；不导出、不上传、不改 PDF。
- **失败如实**：生成失败写进 state（``failed`` + error），未生成/过期在 status 里明说，
  不伪造节点、不假装已启用。生成失败时规划器照旧逐页读原文。

表结构（自建，复用 reader.sqlite3；与 page_masks 同一套备份/迁移口径）：
  doc_navigation(document_id, page, kind, label, created)  主键 (document_id,page,kind)
  doc_navigation_state(document_id, enabled, built_at, source_hash, node_count, last_used,
                       file_stamp, mask_revision, build_state, error)
后四列是为了"说清为什么过期/为什么失败"：只存一个合并哈希时，界面只能写一句
"文档变了"，而用户需要知道是换了文件还是改了无关区域。
"""
import hashlib
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

# ≤80 页同步生成：用户点完"生成索引"立刻拿到结果，不必轮询；再多就先把接口返回，
# 进度由 GET 如实报告（与 page_masks 的 40 页阈值同一思路，数字按"本机标签很便宜"放宽）。
SYNC_LIMIT = 80
# 规划上下文与预览最多带多少条。导航层是提示，不是目录全文：几百条标签塞进规划提示词
# 只会挤占问题与章节大纲的位置，并不会让范围判断更准。
PLANNING_LIMIT = 120
LABEL_LIMIT = 80

TABLE_SQL = """
CREATE TABLE IF NOT EXISTS doc_navigation(
  document_id TEXT NOT NULL,
  page INTEGER NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  created TEXT DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(document_id, page, kind));
CREATE INDEX IF NOT EXISTS doc_navigation_document ON doc_navigation(document_id, page);
CREATE TABLE IF NOT EXISTS doc_navigation_state(
  document_id TEXT PRIMARY KEY,
  enabled INTEGER DEFAULT 0,
  built_at TEXT,
  source_hash TEXT,
  node_count INTEGER DEFAULT 0,
  last_used TEXT,
  file_stamp TEXT DEFAULT '',
  mask_revision TEXT DEFAULT '',
  build_state TEXT DEFAULT '',
  error TEXT DEFAULT '');
"""

# 运行态只放进程内：它回答"这一次生成进行到哪一页"，不需要跨重启保留。
# 进程内状态键是 document_id；测试里每个用例的文献 id 唯一，不会互相污染。
_STATUS = {}
_STATUS_LOCK = threading.RLock()

_WHITESPACE = re.compile(r'\s+')


def connect():
    """复用 reader.sqlite3：导航表与文献、chunks、遮罩规则在同一份本地库里，备份口径一致。"""
    from .settings import storage
    connection = storage()
    connection.row_factory = sqlite3.Row
    ensure_tables(connection)
    return connection


def ensure_tables(connection):
    connection.executescript(TABLE_SQL)


def _now():
    return time.strftime('%Y-%m-%d %H:%M:%S')


def _data_dir():
    """数据目录。与 app.settings.storage 用同一 env 优先级（本模块不 import main，避免循环）。"""
    root = Path(__file__).resolve().parent.parent
    return Path(os.environ.get('READER_DATA_DIR', root / 'data'))


# ---------------------------------------------------------------- 纯函数


def _stamp_text(path_stamp):
    """文件指纹的可比较文本。容忍 None/异常值：调用方可能拿不到 stat（附件缺失）。"""
    if isinstance(path_stamp, (tuple, list)) and len(path_stamp) == 2:
        try:
            return f'{int(path_stamp[0])}:{int(path_stamp[1])}'
        except (TypeError, ValueError):
            pass
    return str(path_stamp)


def source_hash(path_stamp, mask_revision=''):
    """导航表的源版本：文件指纹 + 无关区域规则版本。

    两者一起哈希，是因为它们都会让"页 → 标签"失效：换了文件，页码可能整篇错位；
    改了遮罩，被当成首行的页眉可能从标签里消失/出现。任何一个变了都必须重算。
    """
    digest = hashlib.sha256()
    digest.update(_stamp_text(path_stamp).encode('utf-8'))
    digest.update(b'|')
    digest.update(str(mask_revision or '').encode('utf-8'))
    return digest.hexdigest()


def page_label(text):
    """页面标签：第一条非空文本行，压缩空白，截断 80 字符；没有文本就返回空串。

    **没有文本就不生成节点**：空标签会让导航表看起来"每页都有"，实际什么也没说；
    扫描页没有文本层时，可靠的做法是回退逐页读取，而不是编一个标签。
    """
    if not text:
        return ''
    for line in str(text).replace('\r\n', '\n').replace('\r', '\n').split('\n'):
        line = _WHITESPACE.sub(' ', line).strip()
        if line:
            return line[:LABEL_LIMIT]
    return ''


# ---------------------------------------------------------------- 路径与当前版本


def _document_row(connection, document_id):
    return connection.execute(
        'SELECT id, pages, storage_kind, external_path FROM documents WHERE id=? AND deleted=0',
        (document_id,)).fetchone()


def _path_of(document_id):
    """文献文件路径（与应用内其他路径解析口径一致）；返回 ``(path, 说明)``。

    应用副本在数据目录里；Zotero 只读链接用它登记的外部路径。取 document_path 的
    404/409 语义留给 main.host：这里只判"文件在不在"，不做 stamp 校验。
    """
    with connect() as connection:
        row = _document_row(connection, document_id)
    if row is None:
        return None, '文献记录不存在'
    external = row['external_path'] or ''
    if (row['storage_kind'] or 'copy') == 'zotero' and external:
        return Path(external), ''
    return _data_dir() / f"{document_id}.pdf", ''


def _current(document_id, stamp=None, mask_revision=None):
    """当前的 ``(文件指纹, 遮罩版本)``；文件不可用返回 ``(None, 原因)``。"""
    from . import page_masks
    if mask_revision is None:
        mask_revision = page_masks.revision(document_id)
    if stamp is not None:
        return stamp, mask_revision
    path, detail = _path_of(document_id)
    if path is None:
        return None, detail
    try:
        stat = path.stat()
    except OSError:
        return None, '附件不可用：文件不存在或无法读取'
    return (stat.st_size, stat.st_mtime_ns), mask_revision


def _state_row(document_id):
    with connect() as connection:
        return connection.execute(
            'SELECT * FROM doc_navigation_state WHERE document_id=?', (document_id,)).fetchone()


# ---------------------------------------------------------------- 状态


def status(document_id, stamp=None, mask_revision=None):
    """导航表状态：missing / ready / stale / building / failed。

    ``stamp``/``mask_revision`` 可由调用方传入（规划路径刚 stat 过，不必再 stat 一次）；
    stale 的判定永远以**当前**文件指纹与遮罩版本对比保存的 ``source_hash``——
    过期不是错误，是如实说明"这张表不能再当最新结果用"。
    """
    with _STATUS_LOCK:
        runtime = dict(_STATUS.get(document_id) or {})
    row = _state_row(document_id)
    info = {
        'enabled': bool(row['enabled']) if row is not None else False,
        'node_count': int(row['node_count'] or 0) if row is not None else 0,
        'built_at': (row['built_at'] or '') if row is not None else '',
        'source_hash': (row['source_hash'] or '') if row is not None else '',
        'last_used': (row['last_used'] or '') if row is not None else '',
        'stale_reason': '', 'error': '',
        'done': int(runtime.get('done') or 0), 'total': int(runtime.get('total') or 0),
    }
    if runtime.get('state') == 'building':
        info.update(state='building', error=runtime.get('error') or '')
        return info
    if row is None:
        info['state'] = 'missing'
        return info
    if (row['build_state'] or '') == 'failed':
        info.update(state='failed', error=row['error'] or '未知原因')
        return info
    if not info['source_hash']:
        info['state'] = 'missing'
        return info
    # _current 成功时返回 ``(文件指纹, 遮罩版本)``；失败时第一个元素为 None、第二个是原因。
    current_stamp, current_revision = _current(document_id, stamp=stamp, mask_revision=mask_revision)
    if current_stamp is None:
        info.update(state='stale', stale_reason=current_revision or '附件不可用，无法核对导航索引是否过期')
        return info
    if source_hash(current_stamp, current_revision) != info['source_hash']:
        reasons = []
        if (row['file_stamp'] or '') != _stamp_text(current_stamp):
            reasons.append('文献文件已变化')
        if (row['mask_revision'] or '') != (current_revision or ''):
            reasons.append('无关区域规则已变化')
        if not reasons:
            reasons.append('文献或规则已变化')
        info.update(state='stale', stale_reason='；'.join(reasons) + '：请重建导航索引')
        return info
    info['state'] = 'ready'
    return info


def mark_failed(document_id, error):
    """把失败写进状态：生成出错时界面要显示原因，而不是永远停在"未生成"。"""
    message = str(error or '未知原因')[:300]
    with connect() as connection:
        row = connection.execute(
            'SELECT document_id FROM doc_navigation_state WHERE document_id=?', (document_id,)).fetchone()
        if row is None:
            connection.execute(
                'INSERT INTO doc_navigation_state(document_id, enabled, node_count, build_state, error) '
                "VALUES(?,0,0,'failed',?)", (document_id, message))
        else:
            connection.execute(
                "UPDATE doc_navigation_state SET build_state='failed', error=? WHERE document_id=?",
                (message, document_id))
        connection.commit()
    with _STATUS_LOCK:
        current = dict(_STATUS.get(document_id) or {})
        current.update(state='failed', error=message)
        _STATUS[document_id] = current


def set_enabled(document_id, enabled):
    """启用/关闭导航索引（默认关闭）。开启不代表可用：没生成/过期时状态会如实说明。"""
    value = 1 if enabled else 0
    with connect() as connection:
        row = connection.execute(
            'SELECT document_id FROM doc_navigation_state WHERE document_id=?', (document_id,)).fetchone()
        if row is None:
            connection.execute(
                'INSERT INTO doc_navigation_state(document_id, enabled) VALUES(?,?)',
                (document_id, value))
        else:
            connection.execute(
                'UPDATE doc_navigation_state SET enabled=? WHERE document_id=?', (value, document_id))
        connection.commit()
    return status(document_id)


# ---------------------------------------------------------------- 节点读取


def _node_rows(document_id, limit=PLANNING_LIMIT):
    size = max(1, int(limit))
    with connect() as connection:
        rows = connection.execute(
            'SELECT page, kind, label FROM doc_navigation WHERE document_id=? '
            'ORDER BY page ASC, kind DESC LIMIT ?', (document_id, size)).fetchall()
    return [{'page': int(row['page']), 'kind': row['kind'], 'label': row['label']} for row in rows]


def preview(document_id, limit=PLANNING_LIMIT):
    """节点预览（生成过就返回，不管是否启用）：界面要能在用户开启前先看清楚生成了什么。

    过期时也返回旧节点，但状态是 stale——预览旧表不等于允许它参与规划。
    """
    return _node_rows(document_id, limit)


def _touch_used(document_id):
    """记录最近一次真正被规划使用的时间（只用于开/关对照与审计）。"""
    try:
        with connect() as connection:
            connection.execute(
                'UPDATE doc_navigation_state SET last_used=? WHERE document_id=?',
                (_now(), document_id))
            connection.commit()
    except sqlite3.Error:
        # 使用时间只是审计辅助，写不进去不该让这次提问失败；导航本身仍可能已参与规划。
        pass


def nodes(document_id, limit=PLANNING_LIMIT):
    """规划可用的节点：**只有 ready 且 enabled** 才给。

    未启用、未生成、过期、生成中、失败一律返回空——这是"回退既有逐页路径"的落点：
    规划器拿不到任何落点提示时，就按原有的大纲/检索/整篇口径读取，行为与改造前一致。
    """
    info = status(document_id)
    if info['state'] != 'ready' or not info['enabled']:
        return []
    _touch_used(document_id)
    return _node_rows(document_id, limit)


def _reason(info):
    state = info.get('state')
    if state == 'missing':
        if info.get('enabled'):
            return '导航索引已启用，但还没有生成：请先生成导航索引'
        return '导航索引未生成（默认关闭，需要手动生成并启用）'
    if state == 'stale':
        return '导航索引已过期：' + (info.get('stale_reason') or '文献或无关区域规则已变化')
    if state == 'failed':
        return '导航索引生成失败：' + (info.get('error') or '未知原因')
    if state == 'building':
        return '导航索引正在生成中：%s/%s 页' % (info.get('done') or 0, info.get('total') or 0)
    if state == 'ready':
        return '导航索引已生成但未启用'
    return '导航索引不可用'


def planning_view(document_id, stamp=None):
    """规划上下文里 ``navigation`` 字段的内容：只增不减地给规划器一份可选落点。

    返回 ``{'enabled', 'state', 'nodes', 'reason'}``。enabled=False 时必有 reason，
    且 nodes 为空——规划器看到的就是"这次没有导航提示，按原路径读"。
    """
    info = status(document_id, stamp=stamp)
    if info['state'] == 'ready' and info['enabled']:
        _touch_used(document_id)
        return {'enabled': True, 'state': 'ready',
                'nodes': _node_rows(document_id, PLANNING_LIMIT)}
    return {'enabled': False, 'state': info['state'], 'nodes': [], 'reason': _reason(info)}


# ---------------------------------------------------------------- 生成


def _progress(document_id):
    def report(done, total):
        with _STATUS_LOCK:
            current = dict(_STATUS.get(document_id) or {})
            current.update(state='building', done=int(done), total=int(total), updated=_now())
            _STATUS[document_id] = current
    return report


def _build_nodes(path, stamp, total, masks, report=None):
    """本机规则生成节点：章节标题优先，没有章节的页用**遮罩后**首行。

    参数都是确定的（路径、指纹、遮罩、页数），因此同一份输入永远得到同一张表——
    这是可复现性的基础，也是"不调用模型"这一版能被审计的原因。
    """
    from . import page_masks
    from .reading_adapter import document_structure, native_page
    tree = document_structure(path, stamp, total, masks)
    # 同一页可能有多个标题（章 + 节落在同一页）：主键是 (document_id,page,kind)，
    # 只保留层级最高（level 最小）的那一个作为该页的章节标签，否则后写会覆盖先写，
    # 结果取决于树的顺序而不是"哪一行更像这一页的主题"。
    sections = {}
    for node in tree:
        try:
            page = int(node.get('page'))
        except (TypeError, ValueError):
            continue
        if not 1 <= page <= total:
            continue
        label = page_label(node.get('title') or '')
        if not label:
            continue
        try:
            level = int(node.get('level') or 1)
        except (TypeError, ValueError):
            level = 1
        current = sections.get(page)
        if current is None or level < current['level']:
            sections[page] = {'page': page, 'kind': 'section', 'label': label, 'level': level}
    rows = [{key: value for key, value in sections[page].items() if key != 'level'}
            for page in sorted(sections)]
    for page in range(1, total + 1):
        if page in sections:
            if report:
                report(page, total)
            continue
        # 必须用遮罩后的文本：本机规则版的标签质量几乎全靠这一句——页眉/页码没被滤掉时，
        # 每一页的标签都会变成同一个页眉，导航表看起来"生成了"其实毫无导航价值。
        regions = page_masks.split_regions(masks, page)
        text = native_page(path, stamp, page, regions)['text']
        label = page_label(text)
        if label:
            rows.append({'page': page, 'kind': 'page', 'label': label})
        if report:
            report(page, total)
    rows.sort(key=lambda row: (row['page'], 0 if row['kind'] == 'section' else 1))
    return rows


def _store(document_id, rows, stamp, mask_revision):
    """整张表替换 + 状态落库（同一事务）：不会出现"节点新了但哈希还是旧的"。"""
    now = _now()
    with connect() as connection:
        row = connection.execute(
            'SELECT enabled FROM doc_navigation_state WHERE document_id=?', (document_id,)).fetchone()
        enabled = 1 if (row and int(row['enabled'] or 0)) else 0
        connection.execute('DELETE FROM doc_navigation WHERE document_id=?', (document_id,))
        if rows:
            connection.executemany(
                'INSERT INTO doc_navigation(document_id, page, kind, label, created) VALUES(?,?,?,?,?)',
                [(document_id, node['page'], node['kind'], node['label'], now) for node in rows])
        connection.execute('DELETE FROM doc_navigation_state WHERE document_id=?', (document_id,))
        connection.execute(
            'INSERT INTO doc_navigation_state(document_id, enabled, built_at, source_hash, node_count,'
            ' last_used, file_stamp, mask_revision, build_state, error)'
            " VALUES(?,?,?,?,?,NULL,?,?,'ready','')",
            (document_id, enabled, now, source_hash(stamp, mask_revision), len(rows),
             _stamp_text(stamp), mask_revision or ''))
        connection.commit()


def _worker(document_id, path, stamp, mask_revision, total, masks):
    try:
        rows = _build_nodes(path, stamp, total, masks, _progress(document_id))
    except Exception as exc:  # 后台线程必须把失败写进状态，不能静默吞掉
        mark_failed(document_id, str(exc)[:300])
        return
    try:
        _store(document_id, rows, stamp, mask_revision)
    except Exception as exc:
        mark_failed(document_id, str(exc)[:300])
        return
    with _STATUS_LOCK:
        _STATUS[document_id] = {'state': 'done', 'done': int(total), 'total': int(total),
                                'error': '', 'updated': _now()}


def build(document_id, path, stamp, mask_revision, total, rebuild=False):
    """生成/重建导航表；返回最新状态。

    - 已 ready 且源版本没变、且没要求 rebuild → 直接返回现有结果（幂等，不重算）。
    - ≤``SYNC_LIMIT`` 页同步生成；更多放后台线程，先返回 building，由 GET 轮询进度。
    - 生成失败 → 状态记 failed（带原因），不抛出也不留半张表。
    """
    from . import page_masks
    expected = source_hash(stamp, mask_revision)
    info = status(document_id, stamp=stamp, mask_revision=mask_revision)
    if not rebuild and info['state'] == 'ready' and info['source_hash'] == expected:
        return info
    masks = page_masks.all_regions(document_id)
    total = int(total)
    if total > SYNC_LIMIT:
        with _STATUS_LOCK:
            # 同一文献的后台生成只允许一条：重复点"生成"不该起两个线程同时写同一张表。
            if (_STATUS.get(document_id) or {}).get('state') == 'building':
                return status(document_id, stamp=stamp, mask_revision=mask_revision)
            _STATUS[document_id] = {'state': 'building', 'done': 0, 'total': total,
                                    'error': '', 'updated': _now()}
        threading.Thread(target=_worker,
                         args=(document_id, str(path), stamp, mask_revision, total, masks),
                         daemon=True, name='doc-navigation').start()
        return status(document_id, stamp=stamp, mask_revision=mask_revision)
    # 重建前先清掉上一次的失败状态：这次如果真的又失败，mark_failed 会重新写。
    with _STATUS_LOCK:
        _STATUS[document_id] = {'state': 'building', 'done': 0, 'total': total,
                                'error': '', 'updated': _now()}
    try:
        rows = _build_nodes(str(path), stamp, total, masks, _progress(document_id))
        _store(document_id, rows, stamp, mask_revision)
    except Exception as exc:
        mark_failed(document_id, str(exc)[:300])
        return status(document_id, stamp=stamp, mask_revision=mask_revision)
    with _STATUS_LOCK:
        _STATUS[document_id] = {'state': 'done', 'done': total, 'total': total,
                                'error': '', 'updated': _now()}
    return status(document_id, stamp=stamp, mask_revision=mask_revision)


def delete(document_id):
    """删除导航表与状态：可随时撤销，之后规划路径完全回到改造前的样子。"""
    with connect() as connection:
        connection.execute('DELETE FROM doc_navigation WHERE document_id=?', (document_id,))
        connection.execute('DELETE FROM doc_navigation_state WHERE document_id=?', (document_id,))
        connection.commit()
    with _STATUS_LOCK:
        _STATUS.pop(document_id, None)
    return status(document_id)


def purge_document(document_id):
    """彻底删除文献时清掉它的导航数据（调用方负责；表里不含正文，只是不该留孤儿行）。"""
    delete(document_id)
