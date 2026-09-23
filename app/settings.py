"""Persistent model profiles. Secrets never leave the backend in responses."""
import base64
import ctypes
import ipaddress
import json
import os
import sqlite3
import functools
from contextvars import ContextVar
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr

# 平台预设。kinds 表达"该平台在官方文档中公开提供哪些用途的接口"，
# 而不是"该平台的所有模型都支持这些用途"——具体到模型仍需用户确认并标注能力。
# 这里只登记能以官方文档核实的端点；核不准的一律不写，宁可让用户自行填写，
# 也不要给一个跑不通的预设。平台名仅用于标识兼容的调用方式，不代表任何官方合作。
PROVIDERS = [
    # 百炼的 Embedding 与对话共用 compatible-mode/v1（官方文档：OpenAI Embedding 接口兼容，
    # 模型如 text-embedding-v4 / qwen3.7-text-embedding）。核对记录见 legal/THIRD-PARTY.md
    # 的「已核对的官方来源」一节。
    {"id":"qwen", "name":"通义千问 · 阿里云百炼", "base_url":"https://dashscope.aliyuncs.com/compatible-mode/v1", "kinds":["chat","embedding"], "local":False, "models":[{"id":"text-embedding-v4","capabilities":["embedding"],"source":"官方文档"}]},
    # 智谱的 embeddings 与 chat 同在 /api/paas/v4 下（官方文档：POST .../v4/embeddings，模型 embedding-3）。
    {"id":"zhipu", "name":"智谱 GLM", "base_url":"https://open.bigmodel.cn/api/paas/v4", "kinds":["chat","embedding"], "local":False, "models":[{"id":"embedding-3","capabilities":["embedding"],"source":"官方文档"}]},
    {"id":"doubao", "name":"豆包 · 火山方舟", "base_url":"https://ark.cn-beijing.volces.com/api/v3", "kinds":["chat"], "local":False, "models":[]},
    {"id":"mimo", "name":"小米 MiMo", "base_url":"https://api.xiaomimimo.com/v1", "kinds":["chat"], "local":False, "models":[]},
    {"id":"minimax", "name":"MiniMax", "base_url":"https://api.minimaxi.com/v1", "kinds":["chat"], "local":False, "models":[]},
    {"id":"kimi", "name":"Kimi · 月之暗面", "base_url":"https://api.moonshot.cn/v1", "kinds":["chat"], "local":False, "models":[]},
    # DeepSeek 目前没有公开的 embedding / rerank 接口，故只登记 chat，避免出现"选了却没有模型"。
    {"id":"deepseek", "name":"DeepSeek", "base_url":"https://api.deepseek.com", "kinds":["chat"], "local":False, "models":[]},
    {"id":"siliconflow", "name":"硅基流动", "base_url":"https://api.siliconflow.cn/v1", "kinds":["chat","embedding","rerank"], "local":False, "models":[]},
    {"id":"ollama", "name":"Ollama · 本地模型", "base_url":"http://localhost:11434/v1", "kinds":["chat","embedding"], "local":True, "models":[]},
    {"id":"lmstudio", "name":"LM Studio · 本地模型", "base_url":"http://localhost:1234/v1", "kinds":["chat","embedding"], "local":True, "models":[]},
    {"id":"custom", "name":"自定义 · 兼容 API / 本地服务", "base_url":"", "kinds":["chat","embedding","rerank"], "local":False, "models":[]},
]
_snapshot = ContextVar('model_snapshot', default=None)


def runtime(kind):
    current = _snapshot.get()
    return current[kind] if current is not None else get_profile(kind)


def frozen(fn):
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        if _snapshot.get() is not None:
            return fn(*args, **kwargs)
        token = _snapshot.set({kind:get_profile(kind) for kind in ('chat','embedding','rerank')})
        try:
            return fn(*args, **kwargs)
        finally:
            _snapshot.reset(token)
    return wrapped


class ProfileInput(BaseModel):
    capacity_mode: Literal['auto','manual'] = 'auto'
    tokenizer_id: str = Field(default='',pattern=r'^(?:[a-f0-9]{64})?$')
    visual_reading: bool = False
    # 任务型调用（分批阅读、合并、核对、范围判断）是否关闭"深度思考"。
    # 默认 False＝不发任何厂商私有参数，行为与改造前完全一致；
    # 打开后**只对用户自己配置的这个服务地址**下发，且仅限任务型调用（最终回答仍保留思考）。
    task_thinking: bool = False
    # 同一时间最多几个模型调用在飞（0 = 按服务地址给保守默认：本机 1、远程 2）。
    # 它只影响**同层独立任务**的并发（逐批阅读），不改变调用次数、批数与证据；
    # 上限给太高会被平台限流（429 反而更慢），因此默认留给用户显式设置。
    max_parallel: int = Field(default=0, ge=0, le=8)
    context_window: int = Field(default=65536,ge=8192,le=4000000)
    output_reserve: int = Field(default=4096,ge=512,le=32768)
    max_read_batches: int = Field(default=64,ge=1,le=512)
    connection_id: str = Field(default="",max_length=40)
    enabled: bool = False
    provider: str = Field(default="custom", max_length=40)
    base_url: str = Field(default="", max_length=2048)
    model: str = Field(default="", max_length=300)
    api_key: SecretStr = Field(default_factory=lambda: SecretStr(""))
    clear_key: bool = False


class SettingsInput(BaseModel):
    chat: ProfileInput
    embedding: ProfileInput
    rerank: ProfileInput = Field(default_factory=ProfileInput)


class ProbeInput(ProfileInput):
    kind: Literal["chat", "embedding", "rerank"]


def storage():
    root = Path(__file__).resolve().parent.parent
    folder = Path(os.environ.get('READER_DATA_DIR', root/'data'))
    folder.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder/'reader.sqlite3', timeout=30)
    conn.execute('CREATE TABLE IF NOT EXISTS model_settings(kind TEXT PRIMARY KEY, value TEXT NOT NULL)')
    return conn


def crypt(value, decrypt=False):
    if not value:
        return ""
    if os.name != 'nt':
        # Do not silently fall back to storing a plaintext credential.
        raise HTTPException(503, '此版本的密钥加密保存需要 Windows；本地无密钥模型仍可使用，其他系统可用环境变量。')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]
    raw=base64.b64decode(value) if decrypt else value.encode('utf-8')
    buffer=ctypes.create_string_buffer(raw)
    source=Blob(len(raw),ctypes.cast(buffer,ctypes.POINTER(ctypes.c_ubyte)))
    target=Blob()
    dll=ctypes.WinDLL('crypt32',use_last_error=True)
    fn=dll.CryptUnprotectData if decrypt else dll.CryptProtectData
    fn.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)]
    fn.restype=wintypes.BOOL
    if not fn(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)):
        raise HTTPException(503,'无法读取或加密密钥，请使用保存配置时的 Windows 账户，或重新输入密钥。')
    try:
        result=ctypes.string_at(target.data,target.size)
        return result.decode('utf-8') if decrypt else base64.b64encode(result).decode('ascii')
    finally:
        free=ctypes.WinDLL('kernel32').LocalFree
        free.argtypes=[ctypes.c_void_p]
        free.restype=ctypes.c_void_p
        free(target.data)


def env_profile(kind):
    if kind == 'rerank':
        return {'enabled':False,'provider':'custom','base_url':'','model':'','api_key':'','source':'default'}
    embedding = kind == 'embedding'
    base = os.environ.get('READER_EMBED_BASE' if embedding else 'READER_API_BASE','').rstrip('/')
    model = os.environ.get('READER_EMBED_MODEL' if embedding else 'READER_MODEL', '' if embedding else 'deepseek-chat')
    provider = next((p['id'] for p in PROVIDERS if p['base_url']==base and base), 'custom')
    return {'enabled':bool(base and model), 'provider':provider, 'base_url':base, 'model':model,
            'api_key':os.environ.get('READER_EMBED_KEY' if embedding else 'READER_API_KEY',''), 'source':'environment' if base else 'default'}


def get_profile(kind):
    conn=storage()
    try:
        row=conn.execute('SELECT value FROM model_settings WHERE kind=?',(kind,)).fetchone()
    finally:
        conn.close()
    if not row:
        return env_profile(kind)
    data=json.loads(row[0])
    data['api_key']=crypt(data.pop('encrypted_key',''),decrypt=True)
    if data.get('connection_id'):
        from . import catalog
        shared=catalog.runtime(data['connection_id']);data.update(base_url=shared['base_url'],api_key=shared['api_key'])
    data['source']='saved'
    from .model_metadata import effective
    return effective(data)


def public_profile(profile):
    return {k:v for k,v in profile.items() if k!='api_key'} | {'has_key':bool(profile['api_key'])}


def public_settings():
    return {kind:public_profile(get_profile(kind)) for kind in ('chat','embedding','rerank')} | {'providers':PROVIDERS, 'secret_storage':'Windows 用户账户加密（DPAPI）'}


def validate_url(base):
    try:
        parsed=urlsplit(base)
        if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError()
        _=parsed.port
    except ValueError:
        raise HTTPException(400,'服务地址需为完整的 http(s) URL，不应包含密钥、查询参数或账号密码。')
    if parsed.scheme=='http':
        try:
            ip=ipaddress.ip_address(parsed.hostname)
            private=ip.is_loopback or ip.is_private
        except ValueError:
            private=parsed.hostname=='localhost' or parsed.hostname.endswith('.local')
        if not private:
            raise HTTPException(400,'远程平台请使用 HTTPS；本机或局域网 IP 可使用 HTTP。')
    if parsed.path.rstrip('/').endswith(('/chat/completions','/embeddings','/models')):
        raise HTTPException(400,'请填写服务根地址，例如 http://localhost:11434/v1，不要包含具体接口路径。')


def resolve(kind, body, *, require_model=True):
    if body.output_reserve>body.context_window//2:
        raise HTTPException(400,'输出预留不能超过上下文容量的一半')
    limits={k:getattr(body,k) for k in ('context_window','output_reserve','max_read_batches','tokenizer_id','visual_reading','capacity_mode','task_thinking','max_parallel')}
    if body.connection_id:
        from . import catalog
        if body.enabled and require_model and not body.model.strip():
            raise HTTPException(400,'请选择模型。')
        shared=catalog.runtime(body.connection_id)
        return {'enabled':body.enabled,'provider':'custom','base_url':shared['base_url'],'api_key':shared['api_key'],'model':body.model.strip(),'connection_id':body.connection_id,**limits}
    provider=next((p for p in PROVIDERS if p['id']==body.provider),None)
    if not provider or kind not in provider['kinds']:
        raise HTTPException(400,'此平台不支持所选模型用途，请选择兼容平台。')
    base=body.base_url.strip().rstrip('/')
    model=body.model.strip()
    if base:
        validate_url(base)
    key=body.api_key.get_secret_value().strip()
    if len(key)>8192:
        raise HTTPException(400,'API Key 长度异常，请检查输入。')
    old=get_profile(kind)
    if not key and not body.clear_key and old['base_url']==base and old['provider']==body.provider:
        key=old['api_key']
    if body.clear_key:
        key=''
    if body.enabled:
        if not base or (require_model and not model):
            raise HTTPException(400,'请填写服务地址和模型名称；也可以先获取模型列表。')
        if body.provider in ('deepseek','siliconflow') and not key:
            raise HTTPException(400,'该云平台需要 API Key。')
    return {'enabled':body.enabled,'provider':body.provider,'base_url':base,'model':model,'api_key':key,**limits}


def save(body):
    # Resolve and encrypt both before starting the transaction: no partial saves.
    values={kind:resolve(kind,getattr(body,kind)) for kind in ('chat','embedding','rerank')}
    conn=storage()
    try:
        with conn:
            for kind,profile in values.items():
                encoded={k:v for k,v in profile.items() if k!='api_key'}
                encoded['encrypted_key']='' if profile.get('connection_id') else crypt(profile['api_key'])
                conn.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)',(kind,json.dumps(encoded)))
    finally:
        conn.close()
    return public_settings()


def save_purpose(kind, body):
    """只更新某一个用途，其余用途保持原样。

    用途面板上的"直接选用内置平台预设"只需要设置一个用途；而 save() 要求
    三个用途一起提交（chat/embedding 是必填），也要求前端先把整张表单序列化，
    会把用户尚未保存的其它编辑一并提交。这里按用途合并，避免这两类副作用。
    """
    if kind not in ('chat','embedding','rerank'):
        raise HTTPException(400,'用途无效')
    profile=resolve(kind,body)
    encoded={k:v for k,v in profile.items() if k!='api_key'}
    encoded['encrypted_key']='' if profile.get('connection_id') else crypt(profile['api_key'])
    conn=storage()
    try:
        with conn:
            conn.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)',(kind,json.dumps(encoded)))
    finally:
        conn.close()
    return public_settings()
