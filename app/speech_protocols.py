"""按平台分派的语音调用协议。

语音接口并不是一套统一的 OpenAI 形状，之前把"自定义语音服务"一律当成
``POST {base}/audio/transcriptions`` 与 ``POST {base}/audio/speech``，于是把平台连接换成
MiMo 之后识别与播报都必然失败——MiMo 的两种语音能力都走 ``/chat/completions``。
（用户报告的第 1 项问题。）

按**主机名**分派，而不是按用户在「模型服务」里选的平台 id：用户完全可以把一个
"自定义"连接指向同一台主机；同时厂商专属参数绝不能发到无关端点上——这与
``model_services.task_options`` 是同一条原则。

已核对的官方接口（只实现兼容调用，不复述文档原文，核对记录见 legal/THIRD-PARTY.md
的「已核对的官方来源」一节）：

- MiMo ASR：``POST {base}/chat/completions``，``messages[].content[]`` 用
  ``input_audio`` 内容块传 base64 音频，语种放在 ``asr_options.language``，结果是
  普通 chat 响应的 ``message.content``。
- MiMo TTS：同一个 ``/chat/completions``，待朗读文本放在 ``role=assistant`` 的
  ``content``，音频参数在 ``audio``（``format`` / ``voice``），音频以 base64 出现在
  ``choices[0].message.audio.data``。
- MiniMax ASR：``POST {base}/speech_to_text``（multipart，字段 model + file）。
- MiniMax TTS：``POST {base}/t2a_v2``（JSON），音频以 hex 返回，状态在 ``base_resp``。
- 其余按 OpenAI 兼容的 ``/audio/transcriptions`` 与 ``/audio/speech``。

上游响应体一律不回传（可能回显密钥或用户输入），只转述状态码并给出本程序自己的说明。
"""
import base64
import binascii
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException

from . import model_services

MIMO_HOST = 'api.xiaomimimo.com'
MINIMAX_HOSTS = {'api.minimax.io', 'api.minimaxi.com', 'api.minimax.chat', 'api-uw.minimax.io'}

# 语种取值来自各平台自己的取值范围；本项目界面上的 zh-CN / en-US 需要映射过去。
def _language_code(language, *, mimo=False):
    value = (language or '').strip().lower()
    if value.startswith('zh'):
        return 'zh'
    if value.startswith('en'):
        return 'en'
    return 'auto' if mimo else ''


def protocol(base_url):
    host = (urlsplit(base_url or '').hostname or '').lower()
    if host == MIMO_HOST:
        return 'mimo'
    if host in MINIMAX_HOSTS:
        return 'minimax'
    return 'openai'


def _auth(profile, *, required):
    key = (profile.get('api_key') or '').strip()
    if required and not key:
        raise HTTPException(400, '这条平台连接没有可用密钥，请在「模型服务」重新保存一次 API Key。')
    return {'Authorization': 'Bearer ' + (key or 'local')}


def _failure(exc, what):
    """把上游失败翻译成本程序自己的话，不回传上游响应体。"""
    if isinstance(exc, httpx.TimeoutException):
        return HTTPException(504, what + '超时。云端语音通常几秒内返回；本地服务首次加载模型可能较慢，请重试。')
    if isinstance(exc, httpx.ConnectError):
        return HTTPException(502, what + '连不上服务。请检查「模型服务」里的服务根地址、网络或代理设置。')
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return HTTPException(502, {
            400: what + '被服务拒绝（HTTP 400）：请确认模型名称与音色是否在该平台可用。',
            401: what + '认证失败（HTTP 401）：API Key 无效或已被删除，请在「模型服务」重新保存。',
            402: what + '失败（HTTP 402）：账户余额或额度不足。',
            403: what + '被拒绝（HTTP 403）：这把密钥没有调用该语音接口的权限。',
            404: what + '的接口不存在（HTTP 404）：请确认服务根地址与模型类型。',
            413: what + '失败（HTTP 413）：音频或文本超出平台大小限制。',
            422: what + '失败（HTTP 422）：输入被平台判定为不可处理。',
            429: what + '受限（HTTP 429）：请求过于频繁或额度不足，请稍后重试。',
        }.get(status, what + f'失败（模型服务返回 HTTP {status}）。'))
    return HTTPException(502, what + '失败：服务返回的内容不是预期的语音格式。')


class ProtocolError(ValueError):
    """响应结构不符合该平台协议（不回传原始内容）。"""


def _post(client, url, **kwargs):
    response = client.post(url, **kwargs)
    response.raise_for_status()
    return response


# ---------------------------------------------------------------- MiMo

def _mimo_transcribe(profile, binary, language):
    payload = {
        'model': profile['model'],
        'messages': [{'role': 'user', 'content': [{
            'type': 'input_audio',
            'input_audio': {'data': 'data:audio/wav;base64,' + base64.b64encode(binary).decode('ascii'), 'format': 'wav'},
        }]}],
        'asr_options': {'language': _language_code(language, mimo=True)},
    }
    with httpx.Client(timeout=90, trust_env=model_services.use_proxy(profile)) as client:
        data = _post(client, profile['base_url'] + '/chat/completions',
                     headers=_auth(profile, required=True), json=payload).json()
    return _chat_text(data)


def _chat_text(data):
    """ASR 结果就是普通 chat 响应的正文；只有推理内容不算识别结果。"""
    if not isinstance(data, dict):
        raise ProtocolError('response is not an object')
    rows = data.get('choices')
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ProtocolError('missing choices')
    message = rows[0].get('message') or {}
    content = message.get('content')
    if not isinstance(content, str) or not content.strip():
        raise ProtocolError('empty transcript')
    return content.strip()


def _mimo_synthesize(profile, text, voice):
    model = profile['model']
    audio = {'format': 'wav'}
    if model.endswith('-voicedesign'):
        # 音色设计模型：user 消息是"音色描述"，且不支持 voice 字段。
        messages = [{'role': 'user', 'content': voice.strip() or '用自然清晰的标准普通话朗读。'},
                    {'role': 'assistant', 'content': text}]
    else:
        if model.endswith('-voiceclone'):
            raise HTTPException(400, '音色复刻模型需要一段音频样本作为音色，本程序只提供文字音色字段，'
                                     '请改用 mimo-v2.5-tts（预置音色）或 mimo-v2.5-tts-voicedesign（文字描述音色）。')
        messages = [{'role': 'assistant', 'content': text}]
        audio['voice'] = voice.strip() or 'mimo_default'
    with httpx.Client(timeout=120, trust_env=model_services.use_proxy(profile)) as client:
        data = _post(client, profile['base_url'] + '/chat/completions',
                     headers=_auth(profile, required=True),
                     json={'model': model, 'messages': messages, 'audio': audio}).json()
    if not isinstance(data, dict):
        raise ProtocolError('response is not an object')
    rows = data.get('choices')
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise ProtocolError('missing choices')
    block = (rows[0].get('message') or {}).get('audio')
    encoded = block.get('data') if isinstance(block, dict) else None
    if not isinstance(encoded, str) or not encoded.strip():
        raise ProtocolError('missing audio data')
    try:
        return base64.b64decode(encoded, validate=True), 'audio/wav'
    except (binascii.Error, ValueError):
        raise ProtocolError('audio data is not base64') from None


# ---------------------------------------------------------------- MiniMax

def _minimax_transcribe(profile, binary, language):
    headers = _auth(profile, required=True)
    code = _language_code(language)
    if code:
        headers['language'] = code
    with httpx.Client(timeout=120, trust_env=model_services.use_proxy(profile)) as client:
        data = _post(client, profile['base_url'] + '/speech_to_text', headers=headers,
                     files={'file': ('speech.wav', binary, 'audio/wav')},
                     data={'model': profile['model'], 'response_format': 'json', 'stream': 'false'}).json()
    text = data.get('text') if isinstance(data, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError('missing text')
    return text.strip()


MINIMAX_TTS_CODES = {1000: '服务内部错误', 1001: '请求超时', 1002: '触发限流', 1004: '认证失败',
                     1008: '余额或额度不足', 1039: '超出每分钟额度', 1042: '无效字符超过上限',
                     2013: '请求参数无效'}


# MiMo 官方文档列出的预置音色（voice_id + 说明）。文档是固定清单，不需要请求平台。
MIMO_VOICE_SOURCE = 'https://mimo.mi.com/docs/zh-CN/api/audio/tts'
MIMO_VOICES = [
    ('mimo_default', 'MiMo 默认音色（因部署集群而异）'),
    ('冰糖', '冰糖 · 中文女声'), ('茉莉', '茉莉 · 中文女声'),
    ('苏打', '苏打 · 中文男声'), ('白桦', '白桦 · 中文男声'),
    ('Mia', 'Mia · 英文女声'), ('Chloe', 'Chloe · 英文女声'),
    ('Milo', 'Milo · 英文男声'), ('Dean', 'Dean · 英文男声'),
]
MINIMAX_VOICE_LIMIT = 300


def _mimo_voices(model):
    if model.endswith('-voicedesign'):
        return {'source': '官方文档', 'voices': [], 'metadata_url': MIMO_VOICE_SOURCE,
                'note': 'mimo-v2.5-tts-voicedesign 的这一栏填的是音色描述文字（例如"低沉缓慢的女声"），'
                        '它没有预置音色列表。'}
    if model.endswith('-voiceclone'):
        return {'source': '官方文档', 'voices': [], 'metadata_url': MIMO_VOICE_SOURCE,
                'note': 'mimo-v2.5-tts-voiceclone 需要一段音频样本作为音色，音色名称不是它的取值；'
                        '本程序只提供文字音色字段，请改用预置音色或音色描述模型。'}
    return {'source': '官方文档', 'metadata_url': MIMO_VOICE_SOURCE,
            'voices': [{'id': voice_id, 'label': label} for voice_id, label in MIMO_VOICES],
            'note': '以上是 MiMo 官方文档列出的预置音色，仅 mimo-v2.5-tts 可用；留空即用默认音色。'}


def _minimax_voices(profile):
    """MiniMax 有官方的音色列表接口，会一并返回该账户自己克隆/生成的音色，所以必须实调。"""
    with httpx.Client(timeout=60, trust_env=model_services.use_proxy(profile)) as client:
        data = _post(client, profile['base_url'] + '/get_voice',
                     headers=_auth(profile, required=True), json={'voice_type': 'all'}).json()
    if not isinstance(data, dict):
        raise ProtocolError('response is not an object')
    status = (data.get('base_resp') or {}).get('status_code')
    if status not in (0, None):
        detail = MINIMAX_TTS_CODES.get(status, '平台返回了未列出的错误码')
        raise HTTPException(502, f'获取音色失败：{detail}（平台错误码 {status}）。')
    voices = []
    for group, tag in (('system_voice', ''), ('voice_cloning', '克隆'), ('voice_generation', '生成')):
        rows = data.get(group)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            voice_id = row.get('voice_id')
            if not isinstance(voice_id, str) or not voice_id.strip():
                continue
            name = row.get('voice_name')
            label = voice_id
            if isinstance(name, str) and name.strip():
                label = f'{voice_id}（{name.strip()}）'
            if tag:
                label = f'[{tag}] {label}'
            voices.append({'id': voice_id, 'label': label})
    voices = voices[:MINIMAX_VOICE_LIMIT]
    return {'source': '官方接口', 'voices': voices,
            'note': f'来自你 MiniMax 账户当前可用的音色（共 {len(voices)} 个），含克隆与生成的音色；'
                    '克隆音色需要先成功用于一次语音合成才会出现在列表里。'}


def voices(profile):
    """该平台当前可用的音色。

    只做官方文档或官方接口能支持的事：MiMo 的预置音色是官方文档里的固定清单；MiniMax 有官方的
    音色列表接口（含账户自己的音色，必须实调）；其它平台没有公开的音色列表接口，就如实说明——
    不猜、也不硬编码一份可能已经过期的清单。
    """
    kind = protocol(profile.get('base_url', ''))
    model = (profile.get('model') or '').strip()
    try:
        if kind == 'mimo':
            return _mimo_voices(model)
        if kind == 'minimax':
            return _minimax_voices(profile)
    except HTTPException:
        raise
    except ProtocolError as exc:
        raise HTTPException(502, f'获取音色失败：平台返回的内容不符合预期（{exc}）。') from None
    except Exception as exc:
        raise _failure(exc, '获取音色') from None
    return {'source': '', 'voices': [],
            'note': '该平台没有公开的音色列表接口：请按平台文档把音色名称或音色 ID 填进这一栏。'}


def _minimax_synthesize(profile, text, voice):
    if not voice.strip():
        raise HTTPException(400, 'MiniMax 播报必须指定音色 ID（音色名称）。请在语音设置里填写平台音色列表中的 '
                                 'voice_id，例如 Chinese (Mandarin)_Lyrical_Voice。')
    payload = {
        'model': profile['model'], 'text': text, 'stream': False,
        'output_format': 'hex', 'language_boost': 'auto',
        'voice_setting': {'voice_id': voice.strip(), 'speed': 1, 'vol': 1, 'pitch': 0},
        'audio_setting': {'format': 'wav', 'sample_rate': 32000, 'channel': 1},
    }
    with httpx.Client(timeout=180, trust_env=model_services.use_proxy(profile)) as client:
        data = _post(client, profile['base_url'] + '/t2a_v2',
                     headers=_auth(profile, required=True), json=payload).json()
    if not isinstance(data, dict):
        raise ProtocolError('response is not an object')
    status = (data.get('base_resp') or {}).get('status_code')
    if status not in (0, None):
        detail = MINIMAX_TTS_CODES.get(status, '平台返回了未列出的错误码')
        raise HTTPException(502, f'MiniMax 播报失败：{detail}（平台错误码 {status}）。')
    encoded = (data.get('data') or {}).get('audio')
    if not isinstance(encoded, str) or not encoded.strip():
        raise ProtocolError('missing audio data')
    try:
        return bytes.fromhex(encoded.strip()), 'audio/wav'
    except ValueError:
        raise ProtocolError('audio data is not hex') from None


# ---------------------------------------------------------------- OpenAI 兼容

def _openai_transcribe(profile, binary, language):
    data = {'model': profile['model']}
    with httpx.Client(timeout=90, trust_env=model_services.use_proxy(profile)) as client:
        payload = _post(client, profile['base_url'] + '/audio/transcriptions',
                        headers=model_services.headers(profile),
                        files={'file': ('speech.wav', binary, 'audio/wav')}, data=data).json()
    if not isinstance(payload, dict):
        raise ProtocolError('response is not an object')
    text = payload.get('text')
    if not isinstance(text, str) or not text.strip():
        raise ProtocolError('missing text')
    return {'text': text.strip(), 'confidence': payload.get('confidence')}


def _openai_synthesize(profile, text, voice):
    with httpx.Client(timeout=90, trust_env=model_services.use_proxy(profile)) as client:
        with client.stream('POST', profile['base_url'] + '/audio/speech',
                           headers=model_services.headers(profile),
                           json={'model': profile['model'], 'input': text,
                                 'voice': voice.strip() or 'default', 'response_format': 'wav'}) as response:
            response.raise_for_status()
            out = bytearray()
            for block in response.iter_bytes():
                if len(out) + len(block) > 30 * 1024 * 1024:
                    raise ProtocolError('audio response too large')
                out.extend(block)
            return bytes(out), 'audio/wav'


# ---------------------------------------------------------------- 对外入口

def transcribe(profile, binary, language):
    kind = protocol(profile.get('base_url', ''))
    what = '语音识别'
    try:
        if kind == 'mimo':
            return {'text': _mimo_transcribe(profile, binary, language), 'confidence': None}
        if kind == 'minimax':
            return {'text': _minimax_transcribe(profile, binary, language), 'confidence': None}
        return _openai_transcribe(profile, binary, language)
    except HTTPException:
        raise
    except ProtocolError as exc:
        raise HTTPException(502, f'{what}失败：{_protocol_hint(kind, exc)}') from None
    except Exception as exc:
        raise _failure(exc, what) from None


def synthesize(profile, text, voice):
    kind = protocol(profile.get('base_url', ''))
    what = '语音播报'
    try:
        if kind == 'mimo':
            return _mimo_synthesize(profile, text, voice)
        if kind == 'minimax':
            return _minimax_synthesize(profile, text, voice)
        return _openai_synthesize(profile, text, voice)
    except HTTPException:
        raise
    except ProtocolError as exc:
        raise HTTPException(502, f'{what}失败：{_protocol_hint(kind, exc)}') from None
    except Exception as exc:
        raise _failure(exc, what) from None


def _protocol_hint(kind, exc):
    where = {'mimo': 'MiMo 的语音接口（/chat/completions）',
             'minimax': 'MiniMax 的语音接口',
             'openai': 'OpenAI 兼容语音接口（/audio/transcriptions 与 /audio/speech）'}[kind]
    if str(exc) == 'missing audio data':
        return f'{where}没有返回音频，请确认所选模型确实支持语音播报。'
    if str(exc) == 'audio response too large':
        return '返回的音频超过 30 MB，请缩短文本后重试。'
    return f'{where}返回的内容不符合预期（{exc}），请确认所选模型与用途匹配。'
