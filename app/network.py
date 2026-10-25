"""网络与代理设置：三态配置、实际走向解析与连接自检。

为什么单独一个模块：代理决定的是"请求怎么出去"，而不是"请求发什么"。把它从
model_services 里分出来，调用点只需要一行 ``**model_services.client_options(base_url)``，
三态判断、系统代理读取与凭据打码都集中在这里，便于本机单测（不启动 FastAPI）。

三条产品纪律（改这里之前先读一遍）：

1. **本机/回环/私有网段一律直连**。Ollama、LM Studio、自建 MinerU 都在 localhost
   或局域网里；走代理只会把本机请求绕出去，大概率直接失败。
2. **凭据只在本机使用**：代理地址允许带账号密码（http://user:pass@host:port），
   但日志里不出现、界面与接口响应只返回打码形式（http://user:***@host:port）。
3. **"跟随系统"不是猜**：先看环境变量（与 httpx 的 trust_env 语义一致），没有再看
   Windows 的 WinINET 注册表（httpx 的 trust_env 不读注册表，这正是之前"配了系统代理
   却用不了"的原因）。两边都没有就直连，并如实说明"未检测到"。
"""
import fnmatch
import ipaddress
import json
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit

import httpx

from . import settings

KIND = 'network'
MODES = ('auto', 'manual', 'direct')
MAX_PROXY_LENGTH = 2048
MAX_NO_PROXY_LENGTH = 2000
MAX_NO_PROXY_ITEMS = 100
PROBE_TIMEOUT = 8

# 环境变量名大小写都查：Linux/macOS 社区习惯大写，部分工具用小写。
_ENV_HTTP = ('HTTP_PROXY', 'http_proxy')
_ENV_HTTPS = ('HTTPS_PROXY', 'https_proxy')
_ENV_ALL = ('ALL_PROXY', 'all_proxy')
_ENV_NO_PROXY = ('NO_PROXY', 'no_proxy')

_ROUTE_LABELS = {'manual': '手动代理', 'env': '系统环境代理', 'system': '系统代理注册表', 'none': '直连'}


# ---------------------------------------------------------------- 配置读写

def config():
    """读取已保存的三态配置；没有保存过或内容损坏时回落到默认值（跟随系统）。

    这里刻意**不抛错**：一条损坏的网络设置不应该让整个设置页打不开。
    损坏的数据按默认值处理，界面上"实际走向"会如实显示按什么在走。
    """
    data = {}
    conn = settings.storage()
    try:
        row = conn.execute('SELECT value FROM model_settings WHERE kind=?', (KIND,)).fetchone()
    finally:
        conn.close()
    if row and isinstance(row[0], str):
        try:
            loaded = json.loads(row[0])
        except ValueError:
            loaded = None
        if isinstance(loaded, dict):
            data = loaded
    mode = str(data.get('mode') or 'auto').strip().lower()
    if mode not in MODES:
        mode = 'auto'
    return {'mode': mode, 'proxy_url': str(data.get('proxy_url') or '').strip(),
            'no_proxy': str(data.get('no_proxy') or '').strip()}


def save(payload):
    """校验并保存三态配置，返回 ``public()``（界面会用它刷新"实际走向"）。

    ``payload`` 里的 ``proxy_url`` 可能是界面回传的打码形式（GET 返回的就是打码值）：
    如果它正好等于现有地址的打码结果，就保留原值，避免把 ``***`` 当成真的密码存进去。
    """
    current = config()
    mode = str(payload.get('mode') or current['mode']).strip().lower()
    if mode not in MODES:
        raise ValueError('代理模式无效：只能是「跟随系统」「手动指定」「直连」之一。')
    proxy = str(payload.get('proxy_url', current['proxy_url']) or '').strip()
    if proxy and current['proxy_url'] and proxy == mask_url(current['proxy_url']):
        proxy = current['proxy_url']
    elif proxy:
        # 允许省略协议（用户习惯直接填 127.0.0.1:7890），默认按 http 代理保存。
        proxy = normalize_proxy_url(proxy)
    no_proxy = normalize_no_proxy(payload.get('no_proxy', current['no_proxy']))
    if mode == 'manual' and not proxy:
        raise ValueError('「手动指定」需要填写代理地址，例如 127.0.0.1:7890。')
    conn = settings.storage()
    try:
        with conn:
            conn.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)',
                         (KIND, json.dumps({'mode': mode, 'proxy_url': proxy, 'no_proxy': no_proxy},
                                           ensure_ascii=False)))
    finally:
        conn.close()
    return public()


def public():
    """给界面的公开视图：代理地址打码，附带"检测到什么"与"当前实际走向"。"""
    cfg = config()
    target = default_target()
    return {
        'mode': cfg['mode'],
        'proxy_url': mask_url(cfg['proxy_url']),
        'proxy_url_set': bool(cfg['proxy_url']),
        'has_credentials': _has_userinfo(cfg['proxy_url']),
        'no_proxy': cfg['no_proxy'],
        'modes': [{'id': 'auto', 'label': '跟随系统（自动）', 'hint': '有环境变量代理就用它，否则读取 Windows 系统代理注册表'},
                  {'id': 'manual', 'label': '手动指定代理', 'hint': '所有模型与解析服务请求都走这个代理（本机地址除外）'},
                  {'id': 'direct', 'label': '直连（不使用代理）', 'hint': '忽略环境变量与系统代理'}],
        'detected': detection(),
        'target': target,
        'effective': effective(target),
    }


# ---------------------------------------------------------------- 校验与打码

def validate_proxy(url):
    """校验手动代理地址。报错里绝不回显用户输入（它可能含密码）。"""
    text = str(url or '').strip()
    if len(text) > MAX_PROXY_LENGTH:
        raise ValueError('代理地址过长（最多 2048 字符）。')
    if not text or any(ch.isspace() for ch in text):
        raise ValueError('代理地址不能为空，也不能包含空格。')
    try:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in ('http', 'https'):
            raise ValueError()
        if not parsed.hostname:
            raise ValueError()
        _ = parsed.port  # 端口非法时这里会抛 ValueError
    except ValueError:
        raise ValueError('代理地址需为 http(s) 代理（可省略协议，默认 http），'
                         '例如 127.0.0.1:7890；可带账号密码，但界面只显示打码形式。') from None


def normalize_proxy_url(url):
    """补全协议并校验：``127.0.0.1:7890`` → ``http://127.0.0.1:7890``。"""
    text = str(url or '').strip()
    if text and '://' not in text:
        text = 'http://' + text
    validate_proxy(text)
    return text


def normalize_no_proxy(value):
    """绕过列表归一化：``;``/空白都接受，统一存成逗号分隔的小写条目。"""
    items = []
    for raw in re.split(r'[,;\s]+', str(value or '')):
        item = raw.strip().lower()
        if item and item not in items:
            items.append(item)
    if len(items) > MAX_NO_PROXY_ITEMS:
        raise ValueError(f'绕过列表条目过多（最多 {MAX_NO_PROXY_ITEMS} 条）。')
    text = ','.join(items)
    if len(text) > MAX_NO_PROXY_LENGTH:
        raise ValueError(f'绕过列表过长（最多 {MAX_NO_PROXY_LENGTH} 字符）。')
    return text


def mask_url(url):
    """把 URL 里的 userinfo 打码：``http://user:secret@h:1`` → ``http://user:***@h:1``。"""
    text = str(url or '').strip()
    if not text or '@' not in text:
        return text
    try:
        parsed = urlsplit(text)
    except ValueError:
        return text
    if '@' not in parsed.netloc:
        return text
    userinfo, hostpart = parsed.netloc.rsplit('@', 1)
    user = userinfo.split(':', 1)[0]
    return urlunsplit((parsed.scheme, user + ':***@' + hostpart,
                       parsed.path, parsed.query, parsed.fragment))


def _has_userinfo(url):
    try:
        return '@' in urlsplit(str(url or '')).netloc
    except ValueError:
        return False


# ---------------------------------------------------------------- 系统代理

def _env_value(*names):
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ''


def _with_scheme(value, default='http'):
    text = str(value or '').strip()
    if not text:
        return ''
    if '://' not in text:
        text = default + '://' + text
    return text.rstrip('/')


def env_proxy():
    """环境变量里的代理（大小写都查）。``NO_PROXY`` 也一并带出，用于绕过判断。"""
    return {'http': _with_scheme(_env_value(*_ENV_HTTP)),
            'https': _with_scheme(_env_value(*_ENV_HTTPS)),
            'all': _with_scheme(_env_value(*_ENV_ALL)),
            'no_proxy': _env_value(*_ENV_NO_PROXY)}


def _registry_value(key, name):
    import winreg
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def system_proxy():
    """读取 Windows 的 WinINET 系统代理；非 Windows 或读不到时返回 ``{}``。

    只认 ``ProxyEnable`` / ``ProxyServer`` / ``ProxyOverride`` 三个值：
    - ``ProxyServer`` 的两种写法（``host:port`` 与 ``http=h:1;https=h:2``）都解析；
    - ``ProxyOverride`` 的 ``localhost;127.*;<local>`` 转成绕过列表；
    - **不解析 PAC 脚本**（``AutoConfigURL``）：那需要执行脚本，本版不做，
      读到 PAC 配置时按"未检测到"处理，界面上会如实说明，用户可改用手动代理。
    """
    if os.name != 'nt':
        return {}
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                             r'Software\Microsoft\Windows\CurrentVersion\Internet Settings')
    except OSError:
        return {}
    try:
        enabled = _registry_value(key, 'ProxyEnable')
        server = _registry_value(key, 'ProxyServer')
        override = _registry_value(key, 'ProxyOverride')
    finally:
        try:
            import winreg
            winreg.CloseKey(key)
        except OSError:
            pass
    # ProxyEnable 是 DWORD，但注册表也可能给字符串；注意 0 == False，
    # 必须显式判断，否则 ProxyEnable=0（关闭）会被当成启用。
    if isinstance(enabled, str):
        enabled = enabled.strip().lower() in ('1', 'true')
    else:
        enabled = (enabled is True) or enabled == 1
    if not enabled:
        return {}
    if not isinstance(server, str) or not server.strip():
        return {}
    mapping, bare = {}, ''
    for part in server.split(';'):
        part = part.strip()
        if not part:
            continue
        if '=' in part:
            kind, value = part.split('=', 1)
            kind = kind.strip().lower()
            if kind in ('http', 'https', 'all', 'socks') and value.strip():
                mapping[kind] = _with_scheme(value, 'socks' if kind == 'socks' else 'http')
        else:
            bare = _with_scheme(part)
    if bare:
        for kind in ('http', 'https'):
            mapping.setdefault(kind, bare)
    if not mapping:
        return {}
    return {'enabled': True, 'http': mapping.get('http', ''), 'https': mapping.get('https', ''),
            'all': mapping.get('all', ''), 'socks': mapping.get('socks', ''),
            'no_proxy': _parse_proxy_override(override)}


def _parse_proxy_override(value):
    items = []
    for part in str(value or '').split(';'):
        part = part.strip()
        if not part:
            continue
        item = '<local>' if part == '<local>' else part.lower()
        if item not in items:
            items.append(item)
    return ','.join(items)


def detection():
    """界面上的「检测到什么」：系统代理 / 环境变量 / 一句话结论。"""
    env = env_proxy()
    system = system_proxy()
    info = {
        'platform': os.name,
        'platform_label': 'Windows' if os.name == 'nt' else '非 Windows',
        'env': {'detected': bool(env['http'] or env['https'] or env['all']),
                'http': mask_url(env['http']), 'https': mask_url(env['https']),
                'all': mask_url(env['all']), 'no_proxy': env['no_proxy']},
        'system': {'enabled': False},
        'summary': '',
    }
    if system.get('enabled'):
        info['system'] = {'enabled': True, 'http': mask_url(system.get('http', '')),
                          'https': mask_url(system.get('https', '')),
                          'all': mask_url(system.get('all', '')),
                          'no_proxy': system.get('no_proxy', '')}
    if info['env']['detected']:
        shown = info['env']['https'] or info['env']['http'] or info['env']['all']
        info['summary'] = ('检测到环境变量代理 ' + shown +
                           '：「跟随系统」会优先使用环境变量（与 httpx 的读法一致）。')
    elif system.get('enabled'):
        shown = (info['system'].get('https') or info['system'].get('http')
                 or info['system'].get('all') or '')
        if shown:
            info['summary'] = '检测到 Windows 系统代理 ' + shown + '：「跟随系统」会使用这个注册表设置。'
        else:
            info['summary'] = ('检测到系统代理只配置了 SOCKS：本版不支持 SOCKS，'
                               '「跟随系统」会按直连处理；请改用手动 HTTP 代理。')
    elif os.name == 'nt':
        info['summary'] = '未检测到环境变量代理或 Windows 系统代理：「跟随系统」将直连。'
    else:
        info['summary'] = ('当前系统不是 Windows：只能读取环境变量代理；未检测到环境变量代理，'
                           '「跟随系统」将直连。需要代理请用「手动指定」。')
    return info


# ---------------------------------------------------------------- 实际走向

def _result(option, proxy, source, bypassed, reason):
    return {'option': option, 'proxy': proxy, 'source': source, 'bypassed': bool(bypassed),
            'route': _ROUTE_LABELS[source] if option == 'proxy' else '直连', 'reason': reason}


def is_local_host(host):
    """本机/回环/私有网段判断。与 ``settings.validate_url`` 的私有网段规则一致。"""
    name = str(host or '').strip().strip('[]').lower()
    if not name:
        return True
    if name == 'localhost' or name.endswith('.local'):
        return True
    try:
        ip = ipaddress.ip_address(name)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def host_bypasses(host, no_proxy):
    """目标是否命中绕过列表（NO_PROXY / 手动绕过列表 / ProxyOverride）。"""
    name = str(host or '').strip().lower()
    if not name:
        return False
    for pattern in re.split(r'[,;\s]+', str(no_proxy or '')):
        pattern = pattern.strip().lower()
        if pattern and _pattern_matches(name, pattern):
            return True
    return False


def _pattern_matches(host, pattern):
    if pattern == '*':
        return True
    if pattern == '<local>':
        # Windows 的 <local>: 不带点的主机名（内网机器名）不走代理。
        return '.' not in host
    if '/' in pattern:
        try:
            return ipaddress.ip_address(host) in ipaddress.ip_network(pattern, strict=False)
        except ValueError:
            return False
    candidate = pattern
    # 允许 example.com:8080 这种带端口的写法；IPv6 有多个冒号，不按端口切。
    if candidate.count(':') == 1:
        head, _, port = candidate.partition(':')
        if port.isdigit():
            candidate = head
    if candidate.startswith('*.'):
        candidate = candidate[2:]
    elif candidate.startswith('.'):
        candidate = candidate[1:]
    if not candidate:
        return False
    if any(ch in candidate for ch in '*?['):
        return fnmatch.fnmatchcase(host, candidate)
    if host == candidate:
        return True
    return '.' in candidate and host.endswith('.' + candidate)


def _env_proxy_for(scheme, env):
    if scheme == 'https':
        return env['https'] or env['all']
    return env['http'] or env['all']


def _system_proxy_for(scheme, system):
    if scheme == 'https':
        return system.get('https') or system.get('all') or system.get('http') or ''
    return system.get('http') or system.get('all') or ''


def resolve(target_url):
    """给定请求目标，判断这一跳实际怎么走。

    返回 ``option='direct'|'proxy'``、``proxy``（未打码，内部用来建客户端）、
    ``source='manual'|'env'|'system'|'none'``、``bypassed`` 与中文 ``reason``。
    """
    target = _split_url(target_url)
    if target is None:
        return _result('direct', '', 'none', True, '目标地址无效，按直连处理。')
    host = target.hostname or ''
    scheme = (target.scheme or '').lower()
    if is_local_host(host):
        return _result('direct', '', 'none', True,
                       '目标是本机、回环或私有网段地址：自动直连，不走代理。')
    cfg = config()
    if cfg['mode'] == 'direct':
        return _result('direct', '', 'none', False, '已设置为直连。')
    if cfg['mode'] == 'manual':
        proxy = cfg['proxy_url']
        if not proxy:
            return _result('direct', '', 'none', True, '「手动指定」还没有填写代理地址，暂按直连处理。')
        if host_bypasses(host, cfg['no_proxy']):
            return _result('direct', '', 'manual', True, '目标在绕过列表中，按直连处理。')
        return _result('proxy', proxy, 'manual', False, '手动指定代理。')
    # auto：环境变量优先，其次 Windows 系统代理注册表，最后直连。
    env = env_proxy()
    env_url = _env_proxy_for(scheme, env)
    if env_url:
        if host_bypasses(host, env['no_proxy']):
            return _result('direct', '', 'none', True, '目标在环境变量 NO_PROXY 中，按直连处理。')
        return _result('proxy', env_url, 'env', False, '环境变量代理（跟随系统）。')
    system = system_proxy()
    if system.get('enabled'):
        url = _system_proxy_for(scheme, system)
        if not url:
            return _result('direct', '', 'none', True,
                           '系统代理只配置了 SOCKS：本版不支持 SOCKS，按直连处理；请改用手动 HTTP 代理。')
        if host_bypasses(host, system.get('no_proxy')):
            return _result('direct', '', 'none', True, '系统代理的绕过列表包含此目标，按直连处理。')
        return _result('proxy', url, 'system', False, 'Windows 系统代理（跟随系统）。')
    return _result('direct', '', 'none', False,
                   '未检测到环境变量代理或 Windows 系统代理，按直连处理。')


def client_options(target_url):
    """直接展开给 ``httpx.Client`` 的 kwargs，保证所有调用点只有一行。

    - 环境变量代理：保留 ``trust_env=True``，由 httpx 自己按 NO_PROXY 处理（与改造前一致）；
    - 手动/系统代理：显式 ``proxy`` 且 ``trust_env=False``，避免环境变量再来插一脚；
    - 直连：``trust_env=False``，不让系统环境悄悄把本机请求绕出去。
    """
    result = resolve(target_url)
    if result['option'] == 'proxy':
        if result['source'] == 'env':
            return {'trust_env': True}
        return {'trust_env': False, 'proxy': result['proxy']}
    return {'trust_env': False}


# ---------------------------------------------------------------- 连接自检

def _split_url(url):
    text = str(url or '').strip()
    if not text:
        return None
    try:
        return urlsplit(text)
    except ValueError:
        return None


def validate_target(url):
    """校验测试目标。只允许 http(s)，且不带账号密码与查询参数。"""
    text = str(url or '').strip()
    if not text:
        raise ValueError('请填写要测试的地址。')
    if len(text) > MAX_PROXY_LENGTH:
        raise ValueError('测试地址过长（最多 2048 字符）。')
    if any(ch.isspace() for ch in text):
        raise ValueError('测试地址不能包含空格。')
    try:
        parsed = urlsplit(text)
        if parsed.scheme.lower() not in ('http', 'https'):
            raise ValueError()
        if not parsed.hostname:
            raise ValueError()
        _ = parsed.port
    except ValueError:
        raise ValueError('测试地址需为 http(s) 开头的完整地址，'
                         '例如 https://api.deepseek.com 或 http://127.0.0.1:11434。') from None
    if '@' in parsed.netloc:
        raise ValueError('测试地址不要包含账号密码。')
    if parsed.query or parsed.fragment:
        raise ValueError('测试地址不要带查询参数或锚点（避免把密钥写进地址）。')
    return text


def default_target():
    """默认测试目标＝会话模型的服务根地址。没配置时返回空串（界面会提示先配置）。"""
    try:
        profile = settings.runtime('chat')
        return str(profile.get('base_url') or '').rstrip('/')
    except Exception:
        # 会话模型配置损坏不该把「网络」页也带挂：返回空，由界面提示先修模型设置。
        return ''


def effective(target=''):
    """给界面看的"当前实际走向"（目标地址本身不是秘密，凭据在这里不存在）。"""
    if not target:
        return {'available': False, 'target': '', 'route': '未知', 'option': 'direct',
                'source': 'none', 'proxy': '', 'bypassed': False,
                'reason': '会话模型还没有服务地址：请先在「模型服务」配置平台，或直接填写测试地址。'}
    result = resolve(target)
    return {'available': True, 'target': target, 'route': result['route'],
            'option': result['option'], 'source': result['source'],
            'proxy': mask_url(result['proxy']), 'bypassed': result['bypassed'],
            'reason': result['reason']}


def _probe_message(result):
    """可达时的一句话说明（路线与耗时不写在这里，由前端统一拼，避免两处重复）。"""
    status = result['status_code']
    if status is None:
        return ''
    if 200 <= status < 400:
        return '连接成功（HTTP ' + str(status) + '）。'
    if status in (401, 403):
        return ('网络可达，服务返回 HTTP ' + str(status) + '（需要鉴权）。'
                '自检请求不携带 API Key，所以这是正常现象；'
                '请在「模型服务」里用「测试连接」确认密钥与模型。')
    if status in (404, 405):
        return ('网络可达，服务返回 HTTP ' + str(status) + '。'
                '多数模型服务的根地址本身没有页面，这属于正常；'
                '若实际调用失败，请核对服务根地址与接口路径。')
    if status == 429:
        return '网络可达，但服务返回 HTTP 429（限流或额度不足）。请稍后重试或检查平台额度。'
    if status >= 500:
        return '网络可达，但服务返回 HTTP ' + str(status) + '。请稍后重试或查看服务日志。'
    return '网络可达，服务返回 HTTP ' + str(status) + '。'


def _redact_exception(exc, extra_urls=()):
    """异常文本脱敏：任何 ``scheme://user:pass@`` 都换成 ``***@``，并截断。"""
    text = str(exc) if str(exc).strip() else exc.__class__.__name__
    text = re.sub(r'(?i)(https?://)([^/\s@]+)@', r'\1***@', text)
    for url in extra_urls:
        if url and url in text:
            text = text.replace(url, mask_url(url))
    return text.splitlines()[0][:200]


def _probe_error(exc, result, timeout):
    proxy = mask_url(result['proxy'])
    if isinstance(exc, httpx.TimeoutException):
        return ('连接超时（' + str(timeout) + ' 秒）。请检查网络与目标地址；'
                '目标在受限网络时，请在「网络」里填写代理后重试。')
    if isinstance(exc, (httpx.ConnectError, httpx.ProxyError)):
        if result['option'] == 'proxy':
            return ('连不上目标地址：代理 ' + proxy + ' 可能没有运行、端口不对，'
                    '或代理不允许访问该地址。请核对代理设置，或先改成「直连/跟随系统」再测。')
        return ('连不上目标地址。请确认服务已启动、地址与端口正确；'
                '本机服务请确认没有被防火墙拦截；目标在受限网络时请在「网络」里填写代理。')
    return ('连接失败（' + exc.__class__.__name__ + '：'
            + _redact_exception(exc, (result['proxy'],)) + '）。请检查地址与网络设置。')


def probe(target_url, timeout=PROBE_TIMEOUT):
    """对目标地址发一次只读 GET，报告可达性、实际走向与耗时。

    纪律：
    - **不携带 API Key**，也不带任何用户数据；
    - ``follow_redirects=False``：重定向也算"服务有响应"，不去追第三方地址；
    - 401/403/404 都算"网络可达"（连上了，只是需要鉴权或没有这个页面）；
    - 错误文本先脱敏再返回，代理凭据不会出现在响应或日志里。
    """
    result = resolve(target_url)
    masked_proxy = mask_url(result['proxy'])
    started = time.perf_counter()
    status = None
    error = ''
    try:
        with httpx.Client(timeout=timeout, follow_redirects=False,
                          **client_options(target_url)) as client:
            response = client.get(str(target_url),
                                  headers={'Accept': 'application/json, text/plain, */*',
                                           'User-Agent': 'KanRead-Connection-Probe'})
        status = response.status_code
    except Exception as exc:
        error = _probe_error(exc, result, timeout)
    latency_ms = int(round((time.perf_counter() - started) * 1000))
    report = {'reachable': status is not None, 'status_code': status,
              'route': result['route'], 'option': result['option'],
              'proxy': masked_proxy, 'source': result['source'],
              'bypassed': result['bypassed'], 'reason': result['reason'],
              'latency_ms': latency_ms, 'target': str(target_url),
              'error': error, 'message': ''}
    if status is not None:
        report['message'] = _probe_message(report)
    return report
