"""诊断包：把排查所需的运行环境事实打成一个 zip，且不带走用户内容。

设计口径（对应需求 #1 的第 2 条）：
- **只收集事实**：版本、依赖、操作系统、数据目录结构、各 sqlite 表行数、schema、最近日志、
  最近失败任务。不读文献正文、不读页面文本、不读批注/笔记/译文内容、不读密钥所在的行；
  数据库只执行 ``sqlite_master`` 与 ``count(*)`` 这类语句。
- **必须脱敏**：报告里出现的自由文本（日志行、失败原因、schema）统一过 :func:`sanitize`；
  数据目录与程序目录分别替换成 ``<DATA>`` / ``<APP>``，家目录替换成 ``<HOME>``，
  用户名替换成 ``<USER>``，其余绝对路径替换成 ``<PATH>``，API Key / Bearer 令牌替换成
  ``<REDACTED>``。
- **目录结构只留应用自己的文件名**：数据库、日志、``diagnostics/``、``backups/`` 可以列出；
  用户的 PDF 与其它文件名一律折叠成计数——宁可少给维护者一点信息，也不让诊断包变成数据
  泄露渠道。

生成失败必须如实抛出异常，不能返回一个"看起来成功"的半成品包。
"""
import json
import os
import platform
import re
import sqlite3
import subprocess
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path

from . import logging_setup

_APPLICATION_NAME = '勘读 · KanRead'
_LOG_NAME = re.compile(r'^application\.log(\.\d+)?$')
_MANAGED_DIRECTORIES = ('diagnostics', 'backups', 'tokenizers')
_PACKAGE_PREFIX = 'kanread-diagnostics-'
_MAX_FAILED_ROWS = 10
_MAX_ERROR_CHARS = 300

# 这些模式只针对"看起来就是凭据"的字符串。这里不追求穷举所有平台的密钥格式：
# 真正的保证是**根本不读取保存密钥的那张表**，正则只是日志/异常文本的最后一道闸。
# 每项是 (模式, 替换文本)；带一个捕获组的用占位符保留 URL 前缀，其余整段替换。
_SECRET_PATTERNS = (
    (re.compile(r'(?i)\bsk-[A-Za-z0-9_\-]{6,}'), '<REDACTED>'),
    (re.compile(r'(?i)\b(?:ghp_|gho_|ghu_|xox[bap]-|AKIA)[A-Za-z0-9_\-]{8,}'), '<REDACTED>'),
    (re.compile(r'(?i)\b(api[_-]?key|access[_-]?key|secret[_-]?key|token|password)\b'
                r'\s*[:=]\s*[^\s,;]+'), '<REDACTED>'),
    (re.compile(r'(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}'), '<REDACTED>'),
    (re.compile(r'(?i)(https?://)[^\s/:@]+:[^\s/@]+@'), r'\1<REDACTED>@'),
)
_WINDOWS_PATH = re.compile(r'(?i)(?<![\w])(?:[a-z]:[\\/](?:[^\\/\s:*?"<>|]+[\\/])*[^\\/\s:*?"<>|]*)')
_UNC_PATH = re.compile(r'\\\\[^\s\\]+\\[^\s]*')
_POSIX_PATH = re.compile(r'(?<![\w])/(?:home|Users|root|tmp|var|opt|mnt|srv)/[^\s\'"]*')


def sanitize(text, data_dir=None, app_dir=None):
    """把一段自由文本里的路径、用户名与凭据形态的字符串替换掉。"""
    value = '' if text is None else str(text)
    replacements = []
    for raw, label in ((data_dir, '<DATA>'), (app_dir, '<APP>'), (Path.home(), '<HOME>')):
        if not raw:
            continue
        for variant in {str(raw), str(raw).replace('\\', '/')}:
            if variant:
                replacements.append((variant, label))
    # 先替换较长的路径：数据目录/程序目录可能就在家目录下面，顺序错了会只剩半截路径。
    replacements.sort(key=lambda item: len(item[0]), reverse=True)
    for raw, label in replacements:
        value = value.replace(raw, label)
    username = os.environ.get('USERNAME') or os.environ.get('USER') or ''
    if len(username) >= 3:
        value = re.sub(rf'(?i)(?<![A-Za-z0-9]){re.escape(username)}(?![A-Za-z0-9])', '<USER>', value)
    for pattern, replacement in _SECRET_PATTERNS:
        value = pattern.sub(replacement, value)
    value = _WINDOWS_PATH.sub('<PATH>', value)
    value = _UNC_PATH.sub('<PATH>', value)
    value = _POSIX_PATH.sub('<PATH>', value)
    return value


def _sanitize_tree(value, data_dir=None, app_dir=None):
    """递归清洗报告里的所有字符串（键名是程序自己的，不动）。"""
    if isinstance(value, str):
        return sanitize(value, data_dir, app_dir)
    if isinstance(value, list):
        return [_sanitize_tree(item, data_dir, app_dir) for item in value]
    if isinstance(value, dict):
        return {key: _sanitize_tree(item, data_dir, app_dir) for key, item in value.items()}
    return value


def git_description(app_dir):
    """本地 git 描述；没有 ``.git``、没有 git 命令或不是仓库时返回"未知"。

    只跑 ``git describe``（纯本地读取），不 fetch、不联网。
    """
    try:
        result = subprocess.run(
            ['git', '-C', str(app_dir), 'describe', '--tags', '--always', '--dirty'],
            capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=10,
            env={**os.environ, 'GIT_TERMINAL_PROMPT': '0'})
    except (OSError, subprocess.SubprocessError):
        return '未知'
    description = (result.stdout or '').strip()
    return description if result.returncode == 0 and description else '未知'


def application_info(app_dir):
    from .version import API_VERSION
    return {'name': _APPLICATION_NAME, 'api_version': API_VERSION,
            'git_describe': git_description(app_dir)}


def _lock_file(app_dir):
    for name in ('requirements.lock.txt', 'requirements.txt'):
        path = Path(app_dir) / name
        if path.is_file():
            return path
    return None


def dependency_versions(app_dir):
    """锁定文件里列出的依赖的实际安装版本（没装就如实写"未安装"）。"""
    lock = _lock_file(app_dir)
    if lock is None:
        return {'lock_file': '未找到', 'packages': {}}
    try:
        lines = lock.read_text(encoding='utf-8', errors='replace').splitlines()
    except OSError:
        return {'lock_file': lock.name, 'packages': {'<读取失败>': '未知'}}
    from importlib import metadata
    packages = {}
    for line in lines:
        line = line.strip()
        if not line or line.startswith(('#', '-')):
            continue
        name = re.split(r'[=<>!~\[; ]', line, maxsplit=1)[0].strip()
        if not name:
            continue
        try:
            packages[name] = metadata.version(name)
        except Exception:
            packages[name] = '未安装'
    return {'lock_file': lock.name, 'packages': packages}


def environment_info(app_dir):
    return {'python': platform.python_version(),
            'implementation': platform.python_implementation(),
            'os': platform.platform(),
            'machine': platform.machine(),
            'dependencies': dependency_versions(app_dir)}


def usage(path):
    total = files = directories = 0
    for root, dirnames, filenames in os.walk(path, onerror=lambda error: None):
        directories += len(dirnames)
        for name in filenames:
            files += 1
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return {'bytes': total, 'files': files, 'directories': directories}


def _entry_size(path):
    if path.is_dir():
        return usage(path)['bytes']
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _data_entries(data_dir):
    """数据目录顶层的结构。用户文件名不出现在报告里，只给计数与总字节。"""
    entries = []
    pdf_files = pdf_bytes = other_files = other_bytes = 0
    other_directories = other_dir_bytes = other_dir_files = 0
    for child in sorted(data_dir.iterdir(), key=lambda item: item.name.lower()):
        try:
            if child.is_dir():
                if child.name in _MANAGED_DIRECTORIES:
                    usage_info = usage(child)
                    entries.append({'kind': 'directory', 'name': child.name,
                                    'files': usage_info['files'], 'size_bytes': usage_info['bytes']})
                else:
                    usage_info = usage(child)
                    other_directories += 1
                    other_dir_files += usage_info['files']
                    other_dir_bytes += usage_info['bytes']
            elif child.is_file():
                name = child.name
                if _LOG_NAME.match(name):
                    entries.append({'kind': 'log', 'name': name, 'size_bytes': _entry_size(child)})
                elif name.lower().endswith('.sqlite3'):
                    entries.append({'kind': 'database', 'name': name, 'size_bytes': _entry_size(child)})
                elif name.lower().endswith('.pdf'):
                    pdf_files += 1
                    pdf_bytes += _entry_size(child)
                else:
                    other_files += 1
                    other_bytes += _entry_size(child)
        except OSError:
            continue
    if pdf_files:
        entries.append({'kind': 'documents', 'name': f'<文献 PDF，共 {pdf_files} 个>',
                        'files': pdf_files, 'size_bytes': pdf_bytes})
    if other_files:
        entries.append({'kind': 'other', 'name': f'<其他文件，共 {other_files} 个>',
                        'files': other_files, 'size_bytes': other_bytes})
    if other_directories:
        entries.append({'kind': 'other-directories',
                        'name': f'<其他目录，共 {other_directories} 个>',
                        'files': other_dir_files, 'size_bytes': other_dir_bytes})
    return entries


def _connect_readonly(path):
    """按只读方式打开 sqlite；读不到就返回 None（由调用方如实记一条错误）。"""
    try:
        return sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=5)
    except sqlite3.Error:
        return None


def _databases(data_dir):
    result = []
    for path in sorted(data_dir.glob('*.sqlite3')):
        item = {'name': path.name, 'size_bytes': _entry_size(path), 'tables': [], 'schema': []}
        connection = _connect_readonly(path)
        if connection is None:
            item['error'] = '数据库被占用或无法以只读方式打开'
            result.append(item)
            continue
        try:
            rows = connection.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
            for kind, name, sql in rows:
                if kind != 'table' or name.startswith('sqlite_'):
                    continue
                quoted = '"' + str(name).replace('"', '""') + '"'
                try:
                    count = connection.execute(f'SELECT count(*) FROM {quoted}').fetchone()[0]
                except sqlite3.Error:
                    count = None
                item['tables'].append({'name': name, 'rows': count})
            item['schema'] = [{'type': kind, 'name': name, 'sql': sql} for kind, name, sql in rows]
        except sqlite3.Error as exc:
            item['error'] = f'读取失败：{type(exc).__name__}'
        finally:
            connection.close()
        result.append(item)
    return result


def _log_overview(data_dir, limit):
    files = []
    for path in logging_setup.log_files(data_dir):
        try:
            files.append({'name': path.name, 'size_bytes': path.stat().st_size})
        except OSError:
            continue
    tail = []
    current = logging_setup.log_path(data_dir)
    if current.is_file():
        try:
            with current.open('r', encoding='utf-8', errors='replace') as handle:
                tail = [line.rstrip('\r\n') for line in deque(handle, maxlen=max(1, int(limit)))]
        except OSError as exc:
            tail = [f'<日志读取失败：{type(exc).__name__}>']
    return {'path': str(current), 'files': files, 'tail': tail}


def _failed_tasks(data_dir, log_tail):
    """最近的失败任务摘要。

    应用没有一张"所有后台任务"的失败表，能诚实拿到的只有：译文页的 failed/partial 记录，
    以及日志里的 ERROR 行计数。这里如实说明数据来源，不编造"任务成功/失败"结论。
    """
    failures = []
    database = Path(data_dir) / 'reader.sqlite3'
    if database.is_file():
        connection = _connect_readonly(database)
        if connection is not None:
            try:
                table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='translation_pages'").fetchone()
                if table:
                    rows = connection.execute(
                        "SELECT document_id,page,status,error,updated FROM translation_pages "
                        "WHERE status IN ('failed','partial') ORDER BY updated DESC LIMIT ?",
                        (_MAX_FAILED_ROWS,)).fetchall()
                    failures = [{'document_id': str(document_id), 'page': page, 'status': status,
                                 'error': str(error or '')[:_MAX_ERROR_CHARS], 'updated': str(updated or '')}
                                for document_id, page, status, error, updated in rows]
            except sqlite3.Error:
                failures = []
            finally:
                connection.close()
    error_lines = sum(1 for line in log_tail if ' ERROR ' in line or 'Traceback (most recent call last)' in line)
    return {'translation_pages': failures, 'log_error_lines': error_lines,
            'source': '译文页失败记录 + 日志 ERROR 行；应用没有通用的任务失败表，不在此编造汇总。'}


def _build_report(data_dir, app_dir, log_lines, now):
    log = _log_overview(data_dir, log_lines)
    data = {'path': str(data_dir), 'app_path': str(app_dir),
            'usage': usage(data_dir), 'entries': _data_entries(data_dir),
            'databases': _databases(data_dir)}
    failed = _failed_tasks(data_dir, log['tail'])
    return {'format_version': 1,
            'generated_at': (now or datetime.now()).isoformat(timespec='seconds'),
            'application': application_info(app_dir),
            'environment': environment_info(app_dir),
            'data': data,
            'logs': log,
            'failed_tasks': failed,
            'privacy': {
                'excluded': ['文献正文与页面文本', '批注、笔记与译文内容', 'API Key 与密钥',
                             'PDF 文件本身', '完整绝对路径（已替换为 <DATA>/<APP>/<HOME>/<PATH>）',
                             '用户名（已替换为 <USER>）'],
                'note': '本报告只含运行环境事实与计数，用于排查问题；生成过程不联网。'}}


def _next_target(folder, now):
    stamp = (now or datetime.now()).strftime('%Y%m%d-%H%M%S')
    for attempt in range(1, 100):
        suffix = '' if attempt == 1 else f'-{attempt}'
        target = folder / f'{_PACKAGE_PREFIX}{stamp}{suffix}.zip'
        if not target.exists():
            return target
    raise RuntimeError('诊断包重名过多，未能生成新文件。')


def _format_bytes(count):
    value = float(count or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if value < 1024 or unit == 'GB':
            return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} GB'


def _report_text(report):
    application = report['application']
    environment = report['environment']
    data = report['data']
    lines = [
        f'{_APPLICATION_NAME} 诊断报告',
        '=' * 40,
        f'生成时间：{report["generated_at"]}',
        f'接口版本：v{application["api_version"]}',
        f'程序版本：{application["git_describe"]}',
        f'Python：{environment["python"]}（{environment["implementation"]}）',
        f'操作系统：{environment["os"]}',
        f'数据目录：{data["path"]}（{_format_bytes(data["usage"]["bytes"])}，'
        f'{data["usage"]["files"]} 个文件）',
        '',
        '数据库：',
    ]
    for database in data['databases']:
        lines.append(f'  {database["name"]}（{_format_bytes(database["size_bytes"])}）')
        if database.get('error'):
            lines.append(f'    读取失败：{database["error"]}')
            continue
        for table in database['tables']:
            lines.append(f'    {table["name"]}：{table["rows"]} 行')
    lines.extend(['', '日志：'])
    if report['logs']['files']:
        for log_file in report['logs']['files']:
            lines.append(f'  {log_file["name"]}（{_format_bytes(log_file["size_bytes"])}）')
    else:
        lines.append('  （还没有日志文件）')
    failed = report['failed_tasks']
    lines.append('')
    lines.append('最近失败任务：')
    lines.append(f'  译文失败/部分完成：{len(failed["translation_pages"])} 条')
    lines.append(f'  日志 ERROR 行：{failed["log_error_lines"]} 行')
    for item in failed['translation_pages']:
        lines.append(f'    文献 {item["document_id"]} 第 {item["page"]} 页：{item["status"]} {item["error"]}')
    lines.extend(['', '数据目录内容：'])
    for entry in data['entries']:
        lines.append(f'  [{entry["kind"]}] {entry["name"]}（{_format_bytes(entry["size_bytes"])}）')
    lines.extend(['', '隐私说明：', '  ' + '、'.join(report['privacy']['excluded']) + ' 均不在本报告中。'])
    return '\n'.join(lines) + '\n'


def generate(data_dir, app_dir, *, log_lines=200, now=None):
    """生成诊断包，返回包路径与条目清单。

    ``data_dir`` / ``app_dir`` 由调用方显式传入（不依赖 ``app.main``），便于测试与迁移。
    """
    data_dir = Path(data_dir)
    app_dir = Path(app_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f'数据目录不存在：{data_dir}')
    folder = data_dir / 'diagnostics'
    folder.mkdir(parents=True, exist_ok=True)
    report = _sanitize_tree(_build_report(data_dir, app_dir, log_lines, now), data_dir, app_dir)
    target = _next_target(folder, now)
    log_tail = '\n'.join(report['logs']['tail'])
    with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('report.json', json.dumps(report, ensure_ascii=False, indent=2).encode('utf-8'))
        archive.writestr('report.txt', _report_text(report).encode('utf-8'))
        archive.writestr('logs/application.log.tail.txt', log_tail.encode('utf-8'))
        for database in report['data']['databases']:
            lines = [f'-- {database["name"]}']
            for item in database.get('schema') or []:
                lines.append(f'-- {item["type"]}: {item["name"]}')
                lines.append(item['sql'].rstrip(';') + ';')
                lines.append('')
            archive.writestr(f'database/{database["name"]}.schema.sql', '\n'.join(lines).encode('utf-8'))
    with zipfile.ZipFile(target) as archive:
        entries = archive.namelist()
    return {'path': str(target), 'name': target.name, 'size_bytes': target.stat().st_size,
            'entries': entries, 'created': report['generated_at']}


def list_packages(data_dir):
    """已有诊断包（新的在前）。只列出本模块生成的文件名，不列用户其它文件。"""
    folder = Path(data_dir) / 'diagnostics'
    if not folder.is_dir():
        return []
    items = []
    for path in folder.glob(_PACKAGE_PREFIX + '*.zip'):
        try:
            info = path.stat()
        except OSError:
            continue
        items.append({'name': path.name, 'path': str(path), 'size_bytes': info.st_size,
                      'created': datetime.fromtimestamp(info.st_mtime).isoformat(timespec='seconds')})
    items.sort(key=lambda item: item['created'], reverse=True)
    return items[:50]
