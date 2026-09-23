"""探针用的假翻译服务：只在本机回环上跑，用来观察译文栏在真实浏览器里的排版。

它不是应用的一部分，也不参与测试套件：
- 只监听 127.0.0.1:9911；
- 只实现 OpenAI 兼容的 ``POST /v1/chat/completions``；
- 把请求里的原文回显出来并加一个前缀，**不做任何翻译**，
  因此探针能确认"界面上显示的确实是服务返回的内容"，而不是原文被顶上来充数。
- 默认只回显前 60 个字符（页面短、断言简单）；要看**长篇**译文的排版（长段落会不会被截断、
  分栏会不会把段落从中间劈开）就设 ``PROBE_ECHO_FULL=1``，它会把整段原样回显。
- 设 ``PROBE_DELAY_MS=400`` 可以让它每次回答前等一会儿：这样"整页翻译是并发还是串行"就能用
  墙钟时间量出来（探针里有一组专门测它）。

用法（仓库根目录）：.venv\\Scripts\\python.exe scripts/ui_probe/fake_translate.py
"""
import json
import os
import re
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 9911
PREFIX = '【探针译文】'
ECHO_FULL = os.environ.get('PROBE_ECHO_FULL') == '1'
DELAY = float(os.environ.get('PROBE_DELAY_MS') or 0) / 1000


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = json.loads(self.rfile.read(length) or b'{}')
        user = ''
        for message in reversed(body.get('messages') or []):
            if message.get('role') == 'user':
                user = message.get('content') or ''
                break
        match = re.search(r'【原文】\s*(.+)', user, re.S)
        source = (match.group(1) if match else user).strip().replace('\n', ' ')
        answer = PREFIX + (source if ECHO_FULL else source[:60])
        if DELAY:
            time.sleep(DELAY)
        payload = {'choices': [{'message': {'role': 'assistant', 'content': answer}, 'finish_reason': 'stop'}],
                   'usage': {'prompt_tokens': len(user), 'completion_tokens': len(answer)}}
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        # 「获取模型」用得到：列一个模型就够探针选。
        data = json.dumps({'data': [{'id': 'probe-translate-model'}]}).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        return


if __name__ == '__main__':
    # 必须多线程：应用是**并发**发多段的（整页翻译），单线程的 HTTPServer 会把并发请求排队，
    # 那样量出来的"整页耗时"就还是串行的样子（实测踩过：并发能力被假服务自己掩盖了）。
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()
