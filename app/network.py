"""网络出口：所有外发请求一律直连。

产品决定（2026-10）：**不做代理设置，也不读取系统代理或环境变量代理**。
目标用户以本机模型、局域网模型与国内服务为主；保留"代理"入口只会增加合规误读
与代理凭据保存的风险，收益很小。企业网用户如果需要代理访问云端服务，不是本应用
要解决的主要场景。

调用点仍统一写 ``**network.client_options(base_url)``，保证"直连"只在这里定义一次；
``trust_env=False`` 意味着即使系统里配有 ``HTTP(S)_PROXY``，本程序也不使用。
"""


def client_options(target_url=None):
    """展开给 ``httpx.Client`` 的 kwargs：永远直连。"""
    return {'trust_env': False}
