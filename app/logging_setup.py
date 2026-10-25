"""应用日志：轮转文件处理器，桌面启动器与 start.ps1 两条启动路径共用。

为什么单独一个模块：这台程序有两条启动路径——打包后的「勘读.exe」走
``app/desktop_server.py``，源码运行走 ``start.ps1`` 里的 uvicorn。两条路径都必须把
WARNING 及以上写进 ``data/application.log``，否则用户关掉控制台窗口后就没有任何可排查的
线索。以前 ``logging.basicConfig(filename=...)`` 只挂在桌面路径上，而且没有轮转：日志会
无限增长（本机已经到 100KB+，长期使用可以到 GB 级）。

口径（对应需求 #1 第 1 条）：
- 单文件 2MB，保留 5 份历史（总计约 12MB 上限），UTF-8 编码；
- 级别与格式保持原样（WARNING，``%(asctime)s %(levelname)s %(message)s``）；
- 重复调用 ``configure()`` 不会把同一个文件挂上两个处理器（否则每行日志写两遍）。
"""
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

# 2MB × 5：足够覆盖若干次启动的告警与异常栈，又不会长期吃掉用户磁盘。
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5
FORMAT = '%(asctime)s %(levelname)s %(message)s'
HANDLER_MARK = '_kanread_application_log'


def data_folder(folder=None):
    """日志所在的数据目录：显式参数 > READER_DATA_DIR > 仓库根 data/。"""
    if folder is not None:
        return Path(folder)
    configured = os.environ.get('READER_DATA_DIR')
    return Path(configured) if configured else Path(__file__).resolve().parent.parent / 'data'


def log_path(folder=None):
    return data_folder(folder) / 'application.log'


def log_files(folder=None):
    """现存的日志文件，按时间从旧到新（轮转历史在前，当前文件在最后）。"""
    base = log_path(folder)
    candidates = [base.parent / f'{base.name}.{index}' for index in range(BACKUP_COUNT, 0, -1)]
    candidates.append(base)
    return [path for path in candidates if path.is_file()]


def log_total_size(folder=None):
    total = 0
    for path in log_files(folder):
        try:
            total += path.stat().st_size
        except OSError:
            # 文件刚好被轮转/删除时按 0 计：这里只是给界面一个占用数字，不值得让整个状态接口失败。
            continue
    return total


def configure(folder=None, level=logging.WARNING):
    """把轮转文件日志挂到根 logger，并返回该处理器。

    重复调用是安全的：先摘掉本模块以前挂上的处理器再挂新的，避免日志写两份。
    不碰其他库自己加的处理器——uvicorn 在 ``log_config=None`` 时不会加，但第三方代码可能有。
    """
    folder = data_folder(folder)
    folder.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()
    handler = RotatingFileHandler(log_path(folder), maxBytes=MAX_BYTES,
                                  backupCount=BACKUP_COUNT, encoding='utf-8')
    setattr(handler, HANDLER_MARK, True)
    handler.setFormatter(logging.Formatter(FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
    return handler
