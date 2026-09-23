"""翻译服务配置。

翻译方式只有一种：**调用一个翻译服务**。服务来源两种，都不在这里重复维护地址与密钥：

- 「模型服务」里已连接的平台（含本机 Ollama / LM Studio）——只选连接与模型；
- 用户直接填写的兼容接口——只有这一种情况才需要填地址与密钥。

没有"内置术语表"这种做法：术语替换既不是翻译，也会让用户误以为已经翻过。
"""
import json
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr

from . import settings, translation_engine

KIND = 'translation'
DEFAULT_TARGET = 'zh'

TARGET_LANGUAGES = [
    {'id': 'zh', 'name': '简体中文'},
    {'id': 'en', 'name': 'English'},
    {'id': 'ja', 'name': '日本語'},
    {'id': 'ko', 'name': '한국어'},
    {'id': 'fr', 'name': 'Français'},
    {'id': 'de', 'name': 'Deutsch'},
    {'id': 'es', 'name': 'Español'},
    {'id': 'ru', 'name': 'Русский'},
]


class TranslationSettings(BaseModel):
    target_language: str = Field(default=DEFAULT_TARGET, max_length=30)
    # 服务来源：连接优先；没有连接时才用下面直接填写的地址与模型。
    connection_id: str = Field(default='', max_length=40)
    model: str = Field(default='', max_length=300)
    base_url: str = Field(default='', max_length=2048)
    provider: str = Field(default='custom', max_length=40)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(''))
    clear_key: bool = False


class ProbeBody(BaseModel):
    """试译一句：只发用户点那一下时给出的示例句，不发送任何文献内容。"""

    text: str = Field(default='Deep learning improves the baseline.', max_length=500)
    settings: TranslationSettings | None = None


def defaults():
    return TranslationSettings().model_dump(exclude={'api_key', 'clear_key'})


def config(host=None):
    with settings.storage() as c:
        row = c.execute('SELECT value FROM model_settings WHERE kind=?', (KIND,)).fetchone()
    stored = json.loads(row[0]) if row else {}
    merged = defaults() | {k: v for k, v in stored.items() if not k.startswith('encrypted_')}
    merged['has_key'] = bool(stored.get('encrypted_key'))
    return merged


def public(host=None):
    return {k: v for k, v in config(host).items() if not k.startswith('encrypted_')} | {
        'targets': TARGET_LANGUAGES,
    }


def save(host, body: TranslationSettings):
    old = config(host)
    cfg = body.model_dump(exclude={'api_key', 'clear_key'})
    cfg['base_url'] = (cfg.get('base_url') or '').strip().rstrip('/')
    cfg['model'] = (cfg.get('model') or '').strip()
    cfg['target_language'] = (cfg.get('target_language') or DEFAULT_TARGET).strip() or DEFAULT_TARGET
    if cfg['base_url']:
        settings.validate_url(cfg['base_url'])
    key = body.api_key.get_secret_value().strip()
    if len(key) > 8192:
        raise HTTPException(400, '密钥过长')
    with settings.storage() as c:
        row = c.execute('SELECT value FROM model_settings WHERE kind=?', (KIND,)).fetchone()
    previous = json.loads(row[0]) if row else {}
    # 与 speech.save 同一口径：地址没变且没有要求清空时才沿用旧密钥。
    keep = old.get('base_url') == cfg['base_url'] and not body.clear_key
    if key and not body.clear_key:
        cfg['encrypted_key'] = settings.crypt(key)
    elif keep:
        cfg['encrypted_key'] = previous.get('encrypted_key', '')
    else:
        cfg['encrypted_key'] = ''
    with settings.storage() as c:
        c.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)', (KIND, json.dumps(cfg, ensure_ascii=False)))
    return public(host)


def _stored_key(host=None):
    with settings.storage() as c:
        row = c.execute('SELECT value FROM model_settings WHERE kind=?', (KIND,)).fetchone()
    return (json.loads(row[0]) if row else {}).get('encrypted_key', '')


def profile(host=None):
    """运行期配置：连接优先，其次是用户直接填写的地址。

    额外带上 ``output_reserve``：翻译请求必须**显式给输出预算**，否则会落到服务端的默认
    输出上限（很多兼容服务默认只有 512/1024 token），长段落的译文会被截断——用户报的
    "段落有时候会从中间截断"就是这个（服务的 finish_reason 若是 'length' 我们还能报错，
    但不少服务在这种情况下仍返回 'stop'，于是被静默存下来）。这个数字与聊天用的是同一处设置。
    """
    cfg = config(host)
    reserve = 4096
    try:
        reserve = int((settings.runtime('chat') or {}).get('output_reserve') or 4096)
    except Exception:
        reserve = 4096
    if cfg.get('connection_id'):
        from . import catalog
        shared = catalog.runtime(cfg['connection_id'])
        return {**shared, 'model': cfg.get('model') or '', 'output_reserve': reserve}
    stored_key = _stored_key(host)
    return {'base_url': cfg.get('base_url') or '', 'model': cfg.get('model') or '',
            'api_key': settings.crypt(stored_key, True) if stored_key else '',
            'provider': cfg.get('provider') or 'custom', 'output_reserve': reserve}


def probe(host, body: ProbeBody):
    """试译一句：验证"当前设置能不能真的翻出一句话"，与模型的"测试连接"同一性质。"""
    from . import model_services, translation, translation_engine as engine
    candidate = config(host)
    if body.settings:
        candidate = {**candidate, **body.settings.model_dump(exclude={'api_key', 'clear_key'})}
    saved = None
    with settings.storage() as c:
        row = c.execute('SELECT value FROM model_settings WHERE kind=?', (KIND,)).fetchone()
    saved = row[0] if row else None
    try:
        encoded = {k: v for k, v in candidate.items() if not k.startswith('encrypted_') and k != 'has_key'}
        inline_key = body.settings.api_key.get_secret_value().strip() if body.settings else ''
        encoded['encrypted_key'] = settings.crypt(inline_key) if inline_key else _stored_key(host)
        if not encoded.get('base_url'):
            encoded['base_url'] = (body.settings.base_url if body.settings else '').strip().rstrip('/')
        with settings.storage() as c:
            c.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)', (KIND, json.dumps(encoded, ensure_ascii=False)))
        runtime = profile(host)
        if not (runtime.get('base_url') and runtime.get('model')):
            raise HTTPException(409, '还没有可用的翻译服务：请在「模型服务」里选择一个已连接的模型，'
                                     '或直接填写一个兼容接口的地址与模型。')
        segments = [{'index': 0, 'text': body.text}]
        output = engine.remote_translate(segments, runtime, candidate.get('target_language') or DEFAULT_TARGET)
        return {'ok': True, 'text': output[0],
                'service': translation.model_name(runtime) or runtime.get('base_url', ''),
                'message': '这一句是由所选翻译服务翻出来的。'}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(502, model_services.service_error(exc)) from None
    finally:
        with settings.storage() as c:
            if saved is None:
                c.execute("DELETE FROM model_settings WHERE kind=?", (KIND,))
            else:
                c.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)', (KIND, saved))
