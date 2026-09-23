"""用真实浏览器（Edge headless + CDP）验证批注弹窗到底能不能拖动/缩放。

为什么需要它：用户两次反馈"批注弹窗无法拖动"，而静态检查与 Node 替身都认为接线是对的。
替身测不出"真实 CSS 命中测试""真实指针事件""真实布局"这三件事，所以这里驱动一个真正的
Chromium：载入真实 style.css、注入 experience.js 里**真实的**那段弹窗代码（从源文件现取，
不复制），再用 Input.dispatchMouseEvent 发真实鼠标事件，读回实际样式。

只用标准库：手写一个最小 WebSocket 客户端（CDP 需要）。
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
PORT = 9333


# ---------------------------------------------------------------- 最小 WebSocket 客户端
def ws_connect(url):
    parts = urlsplit(url)
    sock = socket.create_connection((parts.hostname, parts.port or 80), timeout=30)
    key = base64.b64encode(os.urandom(16)).decode()
    path = parts.path + (('?' + parts.query) if parts.query else '')
    handshake = (
        f'GET {path} HTTP/1.1\r\nHost: {parts.hostname}:{parts.port}\r\n'
        'Upgrade: websocket\r\nConnection: Upgrade\r\n'
        f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n')
    sock.sendall(handshake.encode())
    buf = b''
    while b'\r\n\r\n' not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            raise RuntimeError('WebSocket 握手时连接被关闭')
        buf += chunk
    if b' 101 ' not in buf.split(b'\r\n')[0]:
        raise RuntimeError('WebSocket 握手失败：' + buf[:200].decode('utf-8', 'replace'))
    return sock


def ws_send(sock, text):
    payload = text.encode('utf-8')
    mask = os.urandom(4)
    header = b'\x81'
    if len(payload) < 126:
        header += bytes([0x80 | len(payload)])
    elif len(payload) < 65536:
        header += bytes([0x80 | 126]) + struct.pack('>H', len(payload))
    else:
        header += bytes([0x80 | 127]) + struct.pack('>Q', len(payload))
    sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))


def ws_recv(sock):
    def read(n):
        data = b''
        while len(data) < n:
            chunk = sock.recv(n - len(data))
            if not chunk:
                raise EOFError('连接已关闭')
            data += chunk
        return data

    first, second = read(2)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack('>H', read(2))[0]
    elif length == 127:
        length = struct.unpack('>Q', read(8))[0]
    if second & 0x80:
        read(4)
    return read(length).decode('utf-8', 'replace')


class CDP:
    def __init__(self, sock):
        self.sock = sock
        self.next_id = 1
        self.events = []

    def call(self, method, **params):
        message_id = self.next_id
        self.next_id += 1
        ws_send(self.sock, json.dumps({'id': message_id, 'method': method, 'params': params}))
        while True:
            raw = ws_recv(self.sock)
            data = json.loads(raw)
            if data.get('id') == message_id:
                if 'error' in data:
                    raise RuntimeError(f'{method} 失败：{data["error"]}')
                return data.get('result', {})
            if 'method' in data:
                self.events.append(data)

    def evaluate(self, expression):
        result = self.call('Runtime.evaluate', expression=expression, returnByValue=True, awaitPromise=True)
        if result.get('exceptionDetails'):
            detail = result['exceptionDetails']
            raise RuntimeError('页面里抛错：' + json.dumps(detail, ensure_ascii=False)[:600])
        return result.get('result', {}).get('value')


def wait_for_cdp(port, timeout=25):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f'http://127.0.0.1:{port}/json/list', timeout=3) as response:
                return json.loads(response.read().decode('utf-8'))
        except Exception:
            time.sleep(0.6)
    raise RuntimeError('连接不上 Edge 的调试端口')


def main():
    targets = wait_for_cdp(PORT)
    pages = [t for t in targets if t.get('type') == 'page']
    if not pages:
        raise RuntimeError('没有可用的页面目标')
    cdp = CDP(ws_connect(pages[0]['webSocketDebuggerUrl']))
    cdp.call('Page.enable')
    cdp.call('Runtime.enable')
    cdp.call('Log.enable')

    probe = (ROOT / 'scripts' / 'ui_probe' / 'probe.html').as_uri()
    cdp.call('Page.navigate', url=probe)
    for _ in range(60):
        if cdp.evaluate('document.readyState') == 'complete':
            break
        time.sleep(0.2)
    print('页面已加载：', cdp.evaluate('document.title'))

    # 取 experience.js 里真实的那一段（从 const annotationPopover= 到 wireAnnotationPopoverGestures();）
    source = (ROOT / 'static' / 'experience.js').read_text(encoding='utf-8')
    start = source.index("const annotationPopover=el('div','annotation-popover');")
    end = source.index('wireAnnotationPopoverGestures();')
    slice_end = source.index('\n', end) + 1
    slice_code = source[start:slice_end]
    print(f'注入真实代码片段：{len(slice_code)} 字符')

    # 片段必须**顶层**求值：包在 IIFE 里的话 showAnnotationPopover/annotationPopover 都成了局部名字。
    cdp.evaluate(slice_code + ';window.__wired=true;')
    print('片段执行完成，window.__wired =', cdp.evaluate('window.__wired'))
    print('弹窗是否挂在 .reader 下：', cdp.evaluate("!!document.querySelector('.reader .annotation-popover')"))

    cdp.evaluate("""(()=>{showAnnotationPopover([{id:'a1',page:1,style:'highlight',color:'yellow',
      rects:[[0.1,0.1,0.5,0.2]],quote:'睡眠不足会削弱注意力的稳定性。',content:'这条结论需要核对样本'}]);})()""")
    print('弹窗 hidden =', cdp.evaluate('annotationPopover.hidden'))

    report = cdp.evaluate("""(()=>{
      const box=annotationPopover.getBoundingClientRect();
      const head=annotationPopover.querySelector('.annotation-popover-head');
      const headBox=head.getBoundingClientRect();
      const grip=annotationPopover.querySelector('.annotation-popover-grip').getBoundingClientRect();
      const se=annotationPopover.querySelector('.annotation-resize[data-dir="se"]').getBoundingClientRect();
      const e=annotationPopover.querySelector('.annotation-resize[data-dir="e"]').getBoundingClientRect();
      const at=x=>{const node=document.elementFromPoint(x.clientX,x.clientY);return node?node.className+'|'+node.tagName:'null';};
      const centre=r=>({clientX:Math.round(r.left+r.width/2),clientY:Math.round(r.top+r.height/2)});
      return {
        box:{x:Math.round(box.left),y:Math.round(box.top),w:Math.round(box.width),h:Math.round(box.height)},
        headStyle:{display:getComputedStyle(head).display,height:getComputedStyle(head).height,cursor:getComputedStyle(head).cursor},
        popoverStyle:{position:getComputedStyle(annotationPopover).position,overflow:getComputedStyle(annotationPopover).overflow,zIndex:getComputedStyle(annotationPopover).zIndex},
        hitHead:at(centre(headBox)),hitGrip:at(centre(grip)),hitSe:at(centre(se)),hitE:at(centre(e)),
        offsetParent:(annotationPopover.offsetParent||{}).className,
        headCentre:centre(headBox),seCentre:centre(se),
      };
    })()""")
    print('结构报告：', json.dumps(report, ensure_ascii=False, indent=1))

    def mouse(kind, x, y, buttons=0):
        cdp.call('Input.dispatchMouseEvent', type=kind, x=x, y=y, button='left',
                 buttons=buttons, clickCount=1 if kind == 'mousePressed' else 0, pointerType='mouse')

    # 真实鼠标：按住标题栏中心 → 移动 → 松开
    start_point = report['headCentre']
    before = cdp.evaluate("annotationPopover.style.left+'/'+annotationPopover.style.top")
    mouse('mousePressed', start_point['clientX'], start_point['clientY'], buttons=1)
    for step in (20, 40, 60):
        mouse('mouseMoved', start_point['clientX'] + step, start_point['clientY'] + step // 2, buttons=1)
        time.sleep(0.05)
    mouse('mouseReleased', start_point['clientX'] + 60, start_point['clientY'] + 30)
    time.sleep(0.2)
    after = cdp.evaluate("annotationPopover.style.left+'/'+annotationPopover.style.top")
    print(f'拖动标题栏：内联样式 {before} → {after}')
    print('拖动后弹窗位置：', cdp.evaluate("JSON.stringify({x:Math.round(annotationPopover.getBoundingClientRect().left),y:Math.round(annotationPopover.getBoundingClientRect().top)})"))

    # 真实鼠标：拖右下角改大小
    size_before = cdp.evaluate("annotationPopover.style.width+'/'+annotationPopover.style.height")
    corner = cdp.evaluate("(()=>{const r=annotationPopover.querySelector('.annotation-resize[data-dir=\"se\"]').getBoundingClientRect();return {clientX:Math.round(r.left+r.width/2),clientY:Math.round(r.top+r.height/2)};})()")
    mouse('mousePressed', corner['clientX'], corner['clientY'], buttons=1)
    for step in (20, 40):
        mouse('mouseMoved', corner['clientX'] + step, corner['clientY'] + step, buttons=1)
        time.sleep(0.05)
    mouse('mouseReleased', corner['clientX'] + 40, corner['clientY'] + 40)
    time.sleep(0.2)
    size_after = cdp.evaluate("annotationPopover.style.width+'/'+annotationPopover.style.height")
    print(f'拖右下角：内联尺寸 {size_before} → {size_after}')

    print('本机存储：', cdp.evaluate("localStorage.getItem('reader-annotation-popover')"))
    errors = cdp.evaluate('window.__errors')
    print('页面错误：', errors)
    console = [e for e in cdp.events if e.get('method') in ('Runtime.exceptionThrown', 'Log.entryAdded', 'Runtime.consoleAPICalled')]
    if console:
        print('控制台事件：', json.dumps(console, ensure_ascii=False)[:1500])
    return 0


if __name__ == '__main__':
    sys.exit(main())
