"""Internal maintenance feature: logs, local data and update helpers.

独立功能文件：让「日志 / 数据目录 / 备份 / 更新」这些维护能力与阅读逻辑分开，
也便于将来迁移到别的宿主。所有接口只监听本机回环地址，不联网。

对应需求 #1（日志轮转 + 诊断包）、#2（数据与更新安全）、#9（一键更新）：
- 状态里给出版本、数据目录占用、日志大小与轮转口径，界面据此给出可核对的数字；
- 诊断包由 :mod:`app.diagnostics` 生成（已脱敏）；
- 备份由 :mod:`app.backup` 完成，运行中也能用 SQLite backup API 拿到一致快照；
- 「打开数据目录」只调用本机文件管理器，失败如实返回 opened=false 并给出路径。
"""
import os
import subprocess
import sys


def _open_folder(path):
    """在本机文件管理器里打开目录。取不到图形环境时返回 False，由界面提示手动打开。"""
    try:
        if sys.platform.startswith('win'):
            os.startfile(str(path))  # type: ignore[attr-defined]  # 仅 Windows 存在
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(path)])
        else:
            subprocess.Popen(['xdg-open', str(path)])
        return True
    except (OSError, AttributeError):
        return False


def install(router, host):
    from app import backup as backup_module
    from app import diagnostics
    from app.logging_setup import BACKUP_COUNT, MAX_BYTES, log_files, log_path, log_total_size
    from app.version import API_VERSION

    def status_payload():
        data_dir = host.DATA
        app_dir = host.ROOT
        database = data_dir / 'reader.sqlite3'
        files = []
        for path in log_files(data_dir):
            try:
                files.append({'name': path.name, 'size_bytes': path.stat().st_size})
            except OSError:
                continue
        from app.diagnostics import application_info, usage
        return {
            'name': '勘读 · KanRead',
            'api_version': API_VERSION,
            'git_describe': application_info(app_dir)['git_describe'],
            'data_dir': str(data_dir),
            'app_dir': str(app_dir),
            'database': {'path': str(database),
                         'size_bytes': database.stat().st_size if database.is_file() else 0},
            'log': {'path': str(log_path(data_dir)), 'total_size': log_total_size(data_dir),
                    'files': files,
                    'rotation': {'max_bytes': MAX_BYTES, 'backup_count': BACKUP_COUNT}},
            'data_usage': usage(data_dir),
            'diagnostics_dir': str(data_dir / 'diagnostics'),
            'backups_dir': str(data_dir / 'backups'),
            'backups': backup_module.list_backups(data_dir)[:5],
        }

    @router.get('/api/maintenance/status')
    def maintenance_status():
        """版本、数据目录与占用、日志大小、数据库大小。只做 stat，不读取任何用户内容。"""
        return status_payload()

    @router.post('/api/maintenance/diagnostics')
    def create_diagnostics():
        try:
            return diagnostics.generate(host.DATA, host.ROOT)
        except (OSError, RuntimeError, ValueError) as exc:
            raise host.HTTPException(500, '诊断包生成失败：' + str(exc)) from exc

    @router.get('/api/maintenance/diagnostics')
    def diagnostics_packages():
        return {'packages': diagnostics.list_packages(host.DATA),
                'directory': str(host.DATA / 'diagnostics')}

    @router.post('/api/maintenance/backup')
    def create_backup():
        """立即备份数据目录。运行中也能做：数据库走 SQLite backup API 快照。"""
        try:
            return backup_module.create_snapshot(host.DATA)
        except (backup_module.BackupError, OSError, ValueError) as exc:
            raise host.HTTPException(500, '备份失败：' + str(exc)) from exc

    @router.post('/api/maintenance/open-data-dir')
    def open_data_dir():
        opened = _open_folder(host.DATA)
        return {'opened': opened, 'path': str(host.DATA),
                'message': '已在本机文件管理器打开数据目录。' if opened
                else '未能自动打开文件管理器，请按返回的路径手动打开。'}
    return {'maintenance_status': maintenance_status}
