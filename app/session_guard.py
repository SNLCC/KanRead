"""关掉阅读窗口后自动结束后台进程。

用户报告：关掉窗口后后台（`pythonw` 起的无窗口本地进程）仍在运行，**既没有托盘图标、也不会
随窗口关闭退出**。这个后台用户看不见也管不着，是设计缺陷，所以默认行为改为"随窗口关闭退出"。

怎么知道窗口还在不在？两条信号，互为补充：

1. **长连接（主）**：页面打开 `EventSource('/api/session/stream')`。窗口关闭/浏览器退出时连接
   立刻断开，服务端马上就知道——这比定时器可靠：浏览器会把后台/最小化页面的定时器限流到
   约每分钟一次，甚至冻结，定时器方案会在窗口还开着的时候误判成"没人了"。
2. **心跳（兜底）**：页面每 20 秒 `POST /api/session/ping`（EventSource 不可用时仍然有效），
   关闭时再发一个 `navigator.sendBeacon('/api/session/close')`。

退出时机由"停止期限"决定（`_stop_at`）：

- 有活动连接 → 期限清空（不退出）；
- 连接断开（关窗）或收到关闭通知 → `CLOSE_GRACE` 秒后退出；
- 只有心跳没有长连接 → 每次心跳把期限设为 `PING_GRACE` 秒后（明显大于浏览器限流间隔）；
- 服务起来后**还没有任何页面**连上 → `STARTUP_GRACE` 秒后退出（避免"窗口还没打开后台先退了"）。

退出是**优雅**退出：把 uvicorn 的 `Server.should_exit` 置真，不杀进程。
设 `READER_KEEP_SERVER=1` 可关掉这套行为，让后台常驻（旧行为）。
"""
import os
import threading
import time

GRACE = float(os.environ.get('READER_IDLE_EXIT_SECONDS', '20'))          # 兜底宽限（参考值）
CLOSE_GRACE = float(os.environ.get('READER_CLOSE_EXIT_SECONDS', '6'))    # 明确关窗后多久退出
STARTUP_GRACE = float(os.environ.get('READER_STARTUP_GRACE_SECONDS', '60'))
PING_GRACE = float(os.environ.get('READER_PING_EXIT_SECONDS', '90'))     # 只有心跳时容许的最长间隔
RECONNECT_GRACE = float(os.environ.get('READER_RECONNECT_EXIT_SECONDS', '15'))  # 连接意外断开后的等待

_lock = threading.Lock()
_active = 0                     # 当前打开的长连接数
_seen_page = False
_exit_requested = False
_stop_at = None                 # 计划退出的时刻（None = 不退出）
_server = None


def enabled():
    """是否启用"关窗即退出"。默认启用；`READER_KEEP_SERVER=1` 时保持后台常驻。"""
    return os.environ.get('READER_KEEP_SERVER', '').strip().lower() not in ('1', 'true', 'yes', 'on')


def touch():
    """看到一次页面心跳（兜底信号）。"""
    global _seen_page, _stop_at
    if not enabled():
        return
    with _lock:
        _seen_page = True
        # 有长连接时不设期限：连接的断开才是"窗口没了"的准确信号。
        if _active == 0:
            _stop_at = time.monotonic() + PING_GRACE


def connect():
    """页面建立了长连接：窗口还在。"""
    global _active, _seen_page, _stop_at
    _active += 1
    _seen_page = True
    _stop_at = None


def disconnect():
    """长连接断开：`RECONNECT_GRACE` 秒后退出（若还有别的连接则不退）。

    这里比"明确关窗"（`CLOSE_GRACE`）宽一些：长连接可能因为网络抖动、浏览器回收空闲连接
    而短暂断开，页面会在 `retry: 2000` 之后自己重连。真正的关窗会同时触发页面的
    `pagehide` → `closed()`，那条路径用更短的 `CLOSE_GRACE`，所以关窗依旧很快退出。
    """
    global _active, _stop_at
    _active = max(0, _active - 1)
    if _active == 0 and enabled():
        # 用 min：关窗时 beacon（更短的 CLOSE_GRACE）与连接断开几乎同时发生，
        # 谁先谁后不确定，绝不能让后到的"连接断开"把已经更短的期限**推后**。
        deadline = time.monotonic() + RECONNECT_GRACE
        _stop_at = deadline if _stop_at is None else min(_stop_at, deadline)


def closed():
    """页面明确报告关闭（beacon）：比"连接断开"更确定，用更短的 CLOSE_GRACE。

    关窗时 beacon 与连接断开几乎同时发生、顺序不定：连接还挂在 `_active` 里时收到 beacon
    也是正常的，所以 `_active <= 1` 就按"这一条是最后一个窗口"处理（`disconnect()` 用 min()
    不会把期限推后）。还开着两个以上窗口时不猜——那只是其中一个窗口关了。
    """
    global _stop_at
    if not enabled():
        return
    with _lock:
        if _active > 1:
            return
        deadline = time.monotonic() + CLOSE_GRACE
        _stop_at = deadline if _stop_at is None else min(_stop_at, deadline)


def active_connections():
    with _lock:
        return _active


def has_seen_page():
    with _lock:
        return _seen_page


def seconds_until_exit():
    with _lock:
        return None if _stop_at is None else round(_stop_at - time.monotonic(), 3)


def closing():
    """后台是否"马上就要退出"（启动器据此决定要不要先发一次心跳续命）。

    只有期限落在重连宽限之内才算：真正的关窗（beacon 的 CLOSE_GRACE、连接断开的
    RECONNECT_GRACE）算；"刚启动还没有页面"的启动宽限与"还有心跳"的心跳宽限都不算——
    这两种情况下后台还活着，直接复用即可（新页面连上就会清掉期限）。
    """
    if _exit_requested:
        return True
    remaining = seconds_until_exit()
    return remaining is not None and remaining <= RECONNECT_GRACE


def exit_now():
    """请求本机后台优雅退出。"""
    global _exit_requested
    _exit_requested = True
    _stop_server()


def state():
    return {'enabled': enabled(), 'active_connections': active_connections(),
            'seen_page': has_seen_page(), 'closing': closing(),
            'seconds_until_exit': seconds_until_exit(),
            'close_grace': CLOSE_GRACE, 'reconnect_grace': RECONNECT_GRACE,
            'ping_grace': PING_GRACE}


def _arm_startup_deadline():
    global _stop_at
    with _lock:
        if _active == 0 and not _seen_page:
            _stop_at = time.monotonic() + STARTUP_GRACE


def _stop_server():
    server = _server
    if server is not None:
        server.should_exit = True


def watch(server, poll_seconds=1.0):
    """把守护线程挂到 uvicorn 的 Server 上（只在桌面服务入口调用）。"""
    global _server
    _server = server
    if not enabled():
        return None
    # 从这一刻开始计时：模块导入（可能几秒）不算在"没有页面"的宽限里。
    _arm_startup_deadline()
    thread = threading.Thread(target=_loop, args=(poll_seconds,), daemon=True, name='reader-session-guard')
    thread.start()
    return thread


def _expired():
    with _lock:
        if _active:
            return False
        return _stop_at is not None and time.monotonic() >= _stop_at


def _loop(poll_seconds=1.0):
    while True:
        time.sleep(poll_seconds)
        if _exit_requested or _expired():
            _stop_server()
            return
