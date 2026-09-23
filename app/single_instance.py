"""识别并结束"旧版本的后台进程"。

背景（用户实际遇到的问题）：Windows 启动器只在 `/api/health` 没有响应时才启动后台。
于是更新程序后重新双击 exe 也没用——**改文件不会重启已经在跑的进程**：
浏览器从磁盘读到的是新前端，接口却仍旧由旧进程提供，表现成
"我明明重开了程序，界面却提示后台仍是旧版本"。

这里的做法是：按端口找到监听进程 → 先确认该端口上确实是本应用（`/api/health` 的标识）
→ 再确认它是 Python 解释器进程 → 结束它，然后等待端口释放。
任何一步不符合就只记录、不动手——宁可让用户看到提示，也不误杀别的程序。
"""
import json
import subprocess
import time
import urllib.request

MARKER = 'kanread'
# 改名时**必须**把旧标识追加到这里：用户升级时端口上跑着的正是上一代后台，
# 认不出来就会被当成"别人的程序"，于是新后台起不来、或者旧后台永远换不掉
# ——正是本文件开头描述的那个坑。每改一次名追加一项，不要删旧的。
#
# 目前为空：本项目的第一个公开发布版本就用 kanread，没有更早的线上版本需要兼容。
# 但这条规则要留着——它是本文件存在的理由。
LEGACY_MARKERS = ()
DEFAULT_PORT = 8765


def health(port=DEFAULT_PORT, timeout=1.0, host='127.0.0.1'):
    """读取 /api/health；没有服务或返回异常时给空字典。"""
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/api/health', timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8'))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def is_ours(data):
    """这份 /api/health 是不是本应用（新标识或改名前的旧标识）。"""
    return data.get('application') in (MARKER,) + LEGACY_MARKERS


def is_our_app(port=DEFAULT_PORT):
    return is_ours(health(port))


def running_api_version(port=DEFAULT_PORT):
    """端口上本应用的接口版本；不是本应用或没有服务时返回 0。"""
    data = health(port)
    if not is_ours(data):
        return 0
    try:
        return int(data.get('api_version') or 0)
    except (TypeError, ValueError):
        return 0


def listener_pids(port=DEFAULT_PORT, netstat_output=None):
    """监听该端口的进程号。传入 netstat 文本便于单独测试解析。"""
    text = netstat_output if netstat_output is not None else _netstat()
    pids = []
    for line in (text or '').splitlines():
        parts = line.split()
        if len(parts) < 4 or parts[0].upper() != 'TCP':
            continue
        if not parts[1].endswith(f':{port}') or parts[3].upper() != 'LISTENING':
            continue
        if parts[-1].isdigit() and int(parts[-1]) not in pids:
            pids.append(int(parts[-1]))
    return pids


def image_name(pid):
    """进程映像名（小写，含扩展名）。取不到返回空字符串。

    两道路径：tasklist 的 CSV 输出，以及 PowerShell 的 Get-Process。
    本机沙箱会包装子进程，某些 PID 在 tasklist 里查不到，PowerShell 通常仍能取到。
    """
    try:
        result = subprocess.run(['tasklist', '/FI', f'PID eq {pid}', '/FO', 'CSV', '/NH'],
                                capture_output=True, text=True, timeout=20)
        rows = [row for row in (result.stdout or '').strip().splitlines() if row.strip()]
        if rows:
            name = rows[0].split('","')[0].strip('"').strip().lower()
            if name:
                return name
    except Exception:
        pass
    try:
        result = subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command',
                                 f'(Get-Process -Id {pid} -ErrorAction SilentlyContinue).ProcessName'],
                                capture_output=True, text=True, timeout=25)
        name = (result.stdout or '').strip().lower()
        return f'{name}.exe' if name else ''
    except Exception:
        return ''


def _netstat():
    try:
        result = subprocess.run(['netstat', '-ano', '-p', 'tcp'], capture_output=True, text=True, timeout=20)
        return result.stdout
    except Exception:
        return ''


def _kill(pid):
    """结束进程：直接用 TerminateProcess，不派生 taskkill。

    实测（本机沙箱）里 `taskkill` + 管道捕获会卡住不返回；TerminateProcess 不需要子进程、
    也不需要管道，行为确定。非 Windows 退回到 SIGTERM。
    """
    import os
    import signal
    if os.name != 'nt':
        try:
            os.kill(int(pid), signal.SIGTERM)
            return True
        except Exception:
            return False
    import ctypes
    PROCESS_TERMINATE = 0x0001
    try:
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, int(pid))
        if not handle:
            return False
        try:
            return bool(kernel32.TerminateProcess(handle, 1))
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def stop_stale(port=DEFAULT_PORT, expected_version=0, log=None, wait_seconds=8.0):
    """结束旧版本后台。返回 (状态, 说明)。

    状态：``absent`` 端口上没有本应用；``current`` 已经是最新版本（不动它）；
    ``stopped`` 已结束旧后台并腾出端口；``blocked`` 端口仍被占用（多半不是本应用的进程）。

    注意区分两种"版本 0"：**没有服务**，和**旧版本后台（那时还没有 api_version 字段）**。
    后者正是用户遇到的情形——重新双击启动器也不会被替换掉。
    """
    report = log or (lambda message: None)
    data = health(port)
    if not is_ours(data):
        return 'absent', f'端口 {port} 上没有本应用在运行。'
    try:
        version = int(data.get('api_version') or 0)
    except (TypeError, ValueError):
        version = 0
    if expected_version and version >= expected_version:
        return 'current', f'后台已在运行（接口 v{version}），无需重启。'
    pids = listener_pids(port)
    if not pids:
        return 'blocked', f'检测到旧后台（接口 v{version}），但没能找到监听端口 {port} 的进程。'
    killed = []
    skipped = []
    for pid in pids:
        name = image_name(pid)
        if name and not name.startswith('python'):
            skipped.append((pid, name))
            report(f'端口 {port} 的监听进程 {pid}（{name}）不是本应用的 Python 进程，未结束。')
            continue
        if not name:
            # 取不到映像名时仍然结束它：端口上已经确认（/api/health 标识）是本应用，
            # 与启动器的判断口径一致——否则这台机器上永远换不掉旧后台。
            report(f'端口 {port} 的监听进程 {pid} 映像名未知，但已确认是本应用，按旧后台结束。')
        if _kill(pid):
            killed.append(pid)
            report(f'已结束旧后台进程 {pid}（接口 v{version}）。')
    if not killed:
        names = '、'.join(f'{pid}（{name}）' for pid, name in skipped) or '未知'
        return 'blocked', (f'旧后台（接口 v{version}）仍在运行：端口 {port} 上的进程 {names} '
                           f'不是本应用的 Python 进程，未结束，请手动确认。')
    deadline = time.monotonic() + max(1.0, wait_seconds)
    while time.monotonic() < deadline:
        if not is_ours(health(port)):
            return 'stopped', f'已结束旧后台（接口 v{version}），端口 {port} 已释放。'
        time.sleep(0.3)
    return 'blocked', f'旧后台进程已结束，但端口 {port} 仍被占用。'
