"""关掉探针用的那个 Edge 实例（只关它：通过它自己的 CDP 端口发 Browser.close）。

注意：不要用 Stop-Process 杀 msedge，那会把用户自己的浏览器窗口一起关掉。
用法：.venv\\Scripts\\python.exe scripts/ui_probe/close_edge.py
"""
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from drive import CDP, ws_connect  # noqa: E402

PORT = 9333


def main():
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{PORT}/json/version', timeout=3) as response:
            info = json.load(response)
    except Exception as error:                       # noqa: BLE001 —— 探针没在跑就当已经关掉了
        print('调试端口没有响应，视为已关闭：', error)
        return
    cdp = CDP(ws_connect(info['webSocketDebuggerUrl']))
    print('正在关闭：', info.get('Browser'))
    try:
        cdp.call('Browser.close')
    except Exception as error:                       # noqa: BLE001 —— 关闭时连接断开是正常的
        print('关闭请求已发出（连接随之中断是预期行为）：', error)


if __name__ == '__main__':
    main()
