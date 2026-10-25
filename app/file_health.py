"""原始附件健康检查：只做文件系统 stat，不打开 PDF、不联网、不启动服务。

为什么单独一个模块：文献列表每次都会请求附件状态，所以这里必须便宜且**绝不抛异常**——
一条附件缺失的记录不能让整个文献库 500，而用户恰恰是在文件坏了的时候最需要看到列表
（"是哪一篇坏了、坏在哪一步"）。这里只如实报告四种状态，不猜、不自动修。
"""
from pathlib import Path
import os
import re

# 应用内保存了字节副本的存储方式：导入上传（copy）与 Zotero 云端附件下载（zotero_cache）。
# 两者都在 data/{id}.pdf 里有一份副本，导入前/下载来源之后怎么变都与本应用无关。
COPY_KINDS = ('copy', 'zotero_cache')
# Zotero 本机附件只是只读链接，不复制字节：依赖外部文件仍存在、且没有被换过。
LINK_KIND = 'zotero'

# 文献标识只允许 32 位十六进制（app/main.py 里新建文献用的 uuid4().hex）。
# 状态检查虽然只读，也绝不拿未校验的标识去拼路径：这条函数也会被列表接口调用。
_ID = re.compile(r'^[a-f0-9]{32}$')


def _value(row, key, default=''):
    """从 sqlite3.Row 或 dict 里取字段；缺列时返回默认值（旧库/手工构造的行也能查）。"""
    try:
        value = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if value is None else value


def copy_path(document_id, data_dir):
    """应用内副本路径；标识格式异常时返回 None（不拿它拼路径）。"""
    if not isinstance(document_id, str) or not _ID.fullmatch(document_id):
        return None
    return Path(data_dir) / f'{document_id}.pdf'


def _result(state, detail, storage_kind):
    return {'state': state, 'detail': detail, 'storage_kind': storage_kind}


def _copy_state(document_id, data_dir, kind):
    label = 'Zotero 云端附件缓存副本' if kind == 'zotero_cache' else '应用内 PDF 副本'
    path = copy_path(document_id, data_dir)
    if path is None:
        return _result('unknown', '文献标识格式异常，无法定位应用内副本。', kind)
    try:
        if not path.is_file():
            return _result(
                'missing',
                f'{label}不存在（数据目录里找不到这个文件）。'
                '文献列表、批注与笔记仍在；阅读正文、检索与引用定位需要重新连接一份 PDF。',
                kind)
        if not os.access(path, os.R_OK):
            return _result(
                'unknown',
                f'{label}存在，但当前读不到（可能是权限限制或文件正被其他程序占用）。',
                kind)
    except OSError as exc:
        return _result('unknown', f'检查{label}时出错：{exc.strerror or exc}。', kind)
    return _result('ok', f'{label}可用，不依赖导入前的原文件。', kind)


def _link_state(row):
    kind = LINK_KIND
    external = _value(row, 'external_path', '')
    if not isinstance(external, str) or not external.strip():
        return _result('unknown', '这条 Zotero 链接没有记录附件路径，无法检查文件是否还在。', kind)
    path = Path(external.strip())
    try:
        if not path.is_file():
            return _result(
                'missing',
                'Zotero 附件文件不存在：Zotero 可能已被卸载、数据目录被移动，或这个附件已被删除。'
                '应用内索引与批注仍在；可以重新连接一份 PDF，或改从 Zotero 页面重新打开附件。',
                kind)
        if not os.access(path, os.R_OK):
            return _result('unknown', 'Zotero 附件存在，但当前读不到（可能是权限限制或文件正被占用）。', kind)
        stat = path.stat()
    except OSError as exc:
        return _result('unknown', f'检查 Zotero 附件时出错：{exc.strerror or exc}。', kind)
    stamp = f'{stat.st_size}:{stat.st_mtime_ns}'
    recorded = _value(row, 'file_stamp', '')
    if recorded != stamp:
        return _result(
            'changed',
            'Zotero 附件已变化（文件大小或修改时间与建立索引时不同）：现有索引可能已经过期。'
            '重新连接一次即可用当前文件重建索引；也可以先保存为本机副本，之后就不依赖 Zotero。',
            kind)
    return _result('ok', 'Zotero 本地只读链接可用，附件指纹与索引一致。', kind)


def state_of(row, data_dir):
    """报告一篇文献的原始附件状态。

    返回 ``{'state': 'ok'|'missing'|'changed'|'unknown', 'detail': 中文说明, 'storage_kind': 存储方式}``。
    只 stat、不打开 PDF：列表接口每篇都要查一次，读文件内容会把翻列表变得很慢。
    """
    kind = str(_value(row, 'storage_kind', '') or '')
    if kind in COPY_KINDS:
        return _copy_state(_value(row, 'id', ''), data_dir, kind)
    if kind == LINK_KIND:
        return _link_state(row)
    if not kind:
        return _result('unknown', '这条文献记录没有存储方式信息，无法判断附件状态。', kind)
    return _result('unknown', f'未知的存储方式「{kind}」，无法判断附件状态。', kind)
