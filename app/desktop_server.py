"""Console-free server entry used by the Windows application launcher."""
import logging
import sys
from pathlib import Path

import uvicorn

if __name__ == '__main__':
    from app.single_instance import DEFAULT_PORT, stop_stale
    from app.version import API_VERSION

    folder = Path(__file__).resolve().parent.parent / 'data'
    folder.mkdir(exist_ok=True)
    logging.basicConfig(filename=folder / 'application.log', level=logging.WARNING,
                        format='%(asctime)s %(levelname)s %(message)s')
    logger = logging.getLogger('reader.startup')
    # 旧版本的后台进程不会因为"重新打开程序"而消失：启动器只会在没人应答时才拉起新进程。
    # 这里主动检查并结束它，避免出现"界面是新的、接口是旧的"这种最难自查的状态。
    state, detail = stop_stale(DEFAULT_PORT, API_VERSION, log=logger.warning)
    logger.warning('%s：%s', state, detail)
    if state == 'current':
        logger.warning('已有同版本后台在运行，本进程退出。')
        sys.exit(0)
    # 关掉阅读窗口后不要留下看不见的后台（用户报告"窗口关了后台还在跑，既没有托盘图标也
    # 不会退出"）：页面心跳消失后由 session_guard 让本进程优雅退出。
    # 需要后台常驻时设 READER_KEEP_SERVER=1。
    from app import session_guard

    server = uvicorn.Server(uvicorn.Config('app.main:app', host='127.0.0.1', port=DEFAULT_PORT,
                                           log_config=None, access_log=False))
    session_guard.watch(server)
    server.run()
