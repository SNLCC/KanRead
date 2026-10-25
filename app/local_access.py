"""本机访问令牌：只允许"从本应用页面发出的请求"调用 /api/*。

要解决的威胁（不是"防住同一台机器上的所有程序"，那不可能——同一用户能读文件）：
- 你在浏览器里打开的**其他网站**通过 form/img/fetch 向 127.0.0.1 发请求（CSRF）；
- **DNS rebinding** 让攻击者域名解析到 127.0.0.1，从而看起来像"同源"；
- 本机其他**运行在你浏览器里的网页应用**直接调本地接口。

做法：进程启动时生成一次性随机令牌，只注入到本应用页面；页面所有请求带
`X-KanRead-Token` 头。`/api/health` 例外——启动器与单实例检查必须在页面加载前
就能读它，而它不返回任何用户数据。

令牌通过查询参数也接受，且**只**给无法自定义请求头的浏览器 API 用
（EventSource、navigator.sendBeacon）。这些接口只做心跳/关闭，不返回正文。
"""
import hmac
import secrets

HEADER = 'X-KanRead-Token'
TOKEN = secrets.token_urlsafe(32)

# 唯一豁免：启动器/单实例检查在拿到页面之前就要用它，且它只回答"服务在不在"。
EXEMPT_PATHS = frozenset({'/api/health'})


def presented(request):
    """从请求头或查询参数取出令牌（头优先）。"""
    value = request.headers.get(HEADER, '')
    if value:
        return value
    return request.query_params.get('token', '')


def authorized(request):
    """常量时间比较；空令牌一律拒绝（不会因为两边都空而放行）。"""
    candidate = presented(request)
    if not candidate:
        return False
    return hmac.compare_digest(candidate, TOKEN)


def needs_token(path):
    return path.startswith('/api/') and path not in EXEMPT_PATHS
