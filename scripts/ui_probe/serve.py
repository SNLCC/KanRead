"""临时探针服务：用真实 app 跑一份页面，供 CDP 探针检查真实布局里的界面行为。

数据目录指向 scripts/ui_probe/data（临时、已 gitignore），不碰用户的 data/。
只在本机回环、临时端口（8799）上运行，探针跑完即结束——它不是应用的后台服务。
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))          # 以脚本路径运行时，Python 只加了脚本所在目录，仓库根要自己加
os.environ['READER_DATA_DIR'] = str(Path(__file__).resolve().parent / 'data')

import uvicorn  # noqa: E402

from app import main  # noqa: E402

if __name__ == '__main__':
    uvicorn.run(main.app, host='127.0.0.1', port=8799, access_log=False)
