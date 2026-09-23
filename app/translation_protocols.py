"""翻译平台协议适配。

翻译服务只有两种来源，形状都由**用户已经配好的东西**决定，不再内置平台预设：

- **「模型服务」里已连接的平台**（含本机 Ollama / LM Studio）：地址与密钥取自那个连接，
  这里只按 OpenAI 兼容形状发请求；
- **用户直接填写的地址**：只有这一种情况下需要填地址与密钥，请求形状固定为
  ``POST {base}/chat/completions``（OpenAI 兼容）。

刻意**没有**内置 DeepL / Microsoft Translator 等专用协议：那两家在国内网络不可达，
登记了也用不上；用户要用就自己在「模型服务」里添加一个 OpenAI 兼容的连接。
"""
from . import model_services

ROOT = '/chat/completions'
# 输出预算的下限与上限：下限保证长段落能翻完（一段最多 900 字符原文），上限不超过服务端常见限制。
MIN_OUTPUT = 1024
MAX_OUTPUT = 32768


def translate(profile, language, root=ROOT):
    """按 OpenAI 兼容形状翻一段。

    输入只有两个键：``profile['system_prompt']`` 与 ``profile['prompt']``
    （由 ``translation_engine.remote_translate`` 拼好，含目标语言）。
    """
    import httpx
    base = (profile.get('base_url') or '').rstrip('/')
    if not base:
        raise ValueError('未配置翻译服务地址')
    messages = [{'role': 'system', 'content': profile.get('system_prompt') or ''},
                {'role': 'user', 'content': profile.get('prompt') or ''}]
    body = {'model': profile.get('model') or '',
            'messages': [message for message in messages if message['content']],
            'temperature': 0.2,
            # **必须显式给输出预算**：本项目的其它模型调用（会话/识别/语音/搜索规划）都给了，
            # 只有翻译没给，于是落到服务端的默认输出上限——长段落的译文会被截断，而服务端
            # 有时仍返回 finish_reason='stop'，就成了"段落从中间截断"这种静默错误（用户报过）。
            'max_tokens': max(MIN_OUTPUT, min(MAX_OUTPUT, int(profile.get('output_reserve') or 4096)))}
    body.update(model_services.task_options(profile, task=True))
    try:
        with httpx.Client(timeout=90, trust_env=model_services.use_proxy(profile)) as client:
            response = client.post(base + (root or ROOT), headers=model_services.headers(profile), json=body)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:
        from .cancellation import check as raise_if_cancelled
        raise_if_cancelled()
        if isinstance(exc, model_services.ResponseFormatError):
            raise
        raise RuntimeError(model_services.service_error(exc)) from None
    return model_services.response_text(payload)
