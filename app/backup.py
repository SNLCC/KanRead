"""本机数据备份：先用 sqlite 的 backup API 做一致性快照，再整体打包成 zip。

为什么不是直接复制文件：后台运行时 ``reader.sqlite3`` 可能正被写入，直接 copy 会拿到
"一半旧一半新"的数据库文件，备份看起来成功、恢复时才发现损坏。``sqlite3.Connection.backup``
是 SQLite 官方为热备份提供的机制，能在不停止服务的前提下拿到一致快照。

口径（对应需求 #2 的第 1 条）：
- 目标目录 ``data/backups/``，文件名 ``kanread-data-<yyyyMMdd-HHmmss>.zip``；
- 包含 SQLite 快照、PDF 与其它用户文件；``backups/`` 与 ``diagnostics/`` 永不递归打包；
- 日志默认排除（``include_logs=True`` 才带上），因为日志每天都会变、体积增长快；
- 失败一律抛异常，由调用方（脚本/接口）转成非 0 退出码或 500，不生成半成品包。
"""
import os
import shutil
import sqlite3
import time
import zipfile
from pathlib import Path

BACKUP_PREFIX = 'kanread-data-'
_EXCLUDED_TOP_DIRECTORIES = ('backups', 'diagnostics')
_SKIP_SUFFIXES = ('-wal', '-shm', '-journal')


class BackupError(RuntimeError):
    """备份失败（数据目录不存在、数据库读不出来等），调用方必须如实报告。"""


def backup_folder(data_dir):
    return Path(data_dir) / 'backups'


def _snapshot_database(source, target):
    """用 SQLite backup API 做热快照，禁止直接 copy 正在写的库文件。"""
    try:
        connection = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    except sqlite3.Error as exc:
        raise BackupError(f'无法打开数据库 {source.name}：{exc}') from exc
    try:
        destination = sqlite3.connect(str(target), timeout=10)
        try:
            connection.backup(destination)
        finally:
            destination.close()
    except sqlite3.Error as exc:
        raise BackupError(f'数据库快照失败 {source.name}：{exc}') from exc
    finally:
        connection.close()


def _next_target(folder, now=None):
    stamp = time.strftime('%Y%m%d-%H%M%S', time.localtime(now)) if now is not None else time.strftime('%Y%m%d-%H%M%S')
    for attempt in range(1, 100):
        suffix = '' if attempt == 1 else f'-{attempt}'
        target = folder / f'{BACKUP_PREFIX}{stamp}{suffix}.zip'
        if not target.exists():
            return target
    raise BackupError('备份文件重名过多，未能生成新文件。')


def _staging_folder(backup_dir):
    """在备份目录下建临时工作目录。

    不用 ``tempfile.mkdtemp``：Python 3.13 起它按 0o700 建目录，在受限令牌下会写不进去
    （本仓库的 ``scripts/pytest_sandbox_tmp.py`` 记录过同一类问题）。这里显式按 0o777 建，
    并且放在备份目录内——它天然被 ``backups/`` 的排除规则挡住，不会被递归打包。
    """
    stamp = time.strftime('%Y%m%d-%H%M%S')
    for attempt in range(1, 100):
        candidate = backup_dir / f'.staging-{os.getpid()}-{stamp}-{attempt}'
        try:
            candidate.mkdir(mode=0o777)
            return candidate
        except FileExistsError:
            continue
        except OSError as exc:
            raise BackupError(f'无法创建备份临时目录：{exc}') from exc
    raise BackupError('无法创建备份临时目录（重名过多）。')


def _relative_excluded(relative):
    parts = relative.parts
    if parts and parts[0].lower() in _EXCLUDED_TOP_DIRECTORIES:
        return True
    if any(part.lower() in _EXCLUDED_TOP_DIRECTORIES for part in parts[:-1]):
        return True
    return False


def create_snapshot(data_dir, *, include_logs=False, output_folder=None, now=None):
    """备份整个数据目录，返回路径、大小与条目清单。

    ``output_folder`` 只给命令行/测试覆盖目标位置用；默认仍是 ``data/backups/``。
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise BackupError(f'数据目录不存在：{data_dir}')
    folder = Path(output_folder) if output_folder else backup_folder(data_dir)
    folder.mkdir(parents=True, exist_ok=True)
    databases = sorted(path for path in data_dir.glob('*.sqlite3') if path.is_file())
    staging = _staging_folder(folder)
    try:
        database_names = {path.name for path in databases}
        for source in databases:
            _snapshot_database(source, staging / source.name)
        copied = 0
        for path in sorted(data_dir.rglob('*')):
            if not path.is_file():
                continue
            relative = path.relative_to(data_dir)
            if _relative_excluded(relative):
                continue
            if len(relative.parts) == 1 and relative.name in database_names:
                continue  # 已经由 backup API 快照替换
            if path.name.endswith(_SKIP_SUFFIXES):
                continue
            if path.suffix.lower() == '.log' and not include_logs:
                continue
            destination = staging / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied += 1
        target = _next_target(folder, now)
        with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(staging.rglob('*')):
                if path.is_file():
                    archive.write(path, path.relative_to(staging).as_posix())
        with zipfile.ZipFile(target) as archive:
            entries = archive.namelist()
        return {'path': str(target), 'name': target.name, 'size_bytes': target.stat().st_size,
                'entries': entries, 'entries_count': len(entries),
                'databases': [path.name for path in databases], 'files': copied,
                'logs_included': bool(include_logs)}
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def list_backups(data_dir):
    folder = backup_folder(data_dir)
    if not folder.is_dir():
        return []
    items = []
    for path in folder.glob(BACKUP_PREFIX + '*.zip'):
        try:
            info = path.stat()
        except OSError:
            continue
        items.append({'name': path.name, 'path': str(path), 'size_bytes': info.st_size,
                      'created': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info.st_mtime))})
    items.sort(key=lambda item: item['created'], reverse=True)
    return items[:50]
