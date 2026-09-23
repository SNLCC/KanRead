"""System speech bridge and opt-in custom ASR/TTS. No bundled proprietary weights."""
import json,os,re,subprocess,tempfile,wave,io
from pathlib import Path
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel,Field,SecretStr
from . import settings,model_services,speech_protocols

class SpeechSettings(BaseModel):
    asr_connection_id:str=Field(default="",max_length=40)
    tts_connection_id:str=Field(default="",max_length=40)
    input_enabled:bool=False
    output_enabled:bool=False
    auto_send:bool=True
    auto_read:bool=False
    correction:bool=True
    context:bool=False
    language:str=Field(default='zh-CN',max_length=30)
    asr_mode:Literal['system','custom']='system'
    tts_mode:Literal['system','custom']='system'
    asr_url:str=Field(default='',max_length=2048)
    tts_url:str=Field(default='',max_length=2048)
    asr_model:str=Field(default='',max_length=200)
    tts_model:str=Field(default='',max_length=200)
    voice:str=Field(default='',max_length=200)
    asr_key:SecretStr=SecretStr('')
    tts_key:SecretStr=SecretStr('')
    clear_asr:bool=False
    clear_tts:bool=False

def config():
    defaults=SpeechSettings().model_dump(exclude={'asr_key','tts_key','clear_asr','clear_tts'})
    with settings.storage() as c:row=c.execute("SELECT value FROM model_settings WHERE kind='speech'").fetchone()
    return defaults| (json.loads(row[0]) if row else {})

def public():
    cfg=config();return {k:v for k,v in cfg.items() if not k.startswith('encrypted_')}|{k+'_has_key':bool(cfg.get('encrypted_'+k)) for k in ('asr','tts')}

def save(body):
    old=config();cfg=body.model_dump(exclude={'asr_key','tts_key','clear_asr','clear_tts'})
    for kind in ('asr','tts'):
        url=cfg[kind+'_url'].strip().rstrip('/');cfg[kind+'_url']=url
        if url:settings.validate_url(url)
        if cfg[kind+'_mode']=='custom' and (not url or not cfg[kind+'_model']):raise HTTPException(400,'自定义语音服务需要地址和模型名称')
        key=getattr(body,kind+'_key').get_secret_value().strip()
        if len(key)>8192:raise HTTPException(400,'密钥过长')
        keep=old.get(kind+'_url')==url and not getattr(body,'clear_'+kind)
        cfg['encrypted_'+kind]=settings.crypt(key) if key and not getattr(body,'clear_'+kind) else old.get('encrypted_'+kind,'') if keep else ''
    with settings.storage() as c:c.execute("INSERT OR REPLACE INTO model_settings VALUES('speech',?)",(json.dumps(cfg),))
    return public()

def system(payload):
    if os.name!='nt':raise HTTPException(400,'基础语音需要 Windows 系统语音组件；其他系统请配置自定义服务')
    script=Path(__file__).with_name('system_speech.ps1').read_text(encoding='utf-8-sig')
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-NonInteractive','-Command',script],input=json.dumps(payload,ensure_ascii=False).encode('utf-8'),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=90,creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise ValueError()
        return json.loads(result.stdout.decode('utf-8-sig'))
    except Exception:raise HTTPException(502,'系统语音处理失败。请检查对应语言的 Windows 语音识别/语音包，或配置自定义服务。') from None

def runtime(kind,cfg):
    if cfg.get(kind+'_connection_id'):
        from . import catalog
        return catalog.runtime(cfg[kind+'_connection_id'])|{'model':cfg[kind+'_model']}
    return {'base_url':cfg[kind+'_url'],'api_key':settings.crypt(cfg.get('encrypted_'+kind,''),True),'model':cfg[kind+'_model'],'provider':'custom'}

def transcribe(binary):
    cfg=config()
    if not cfg['input_enabled']:raise HTTPException(403,'语音输入未开启')
    if len(binary)>8*1024*1024:raise HTTPException(413,'录音过长，最多 60 秒')
    try:
        with wave.open(io.BytesIO(binary)) as wav:
            if wav.getnchannels()!=1 or wav.getsampwidth()!=2 or not 8000<=wav.getframerate()<=48000 or wav.getnframes()/wav.getframerate()>61:raise ValueError()
    except Exception:raise HTTPException(400,'需要最长 60 秒的单声道 PCM WAV') from None
    if cfg['asr_mode']=='system':
        with tempfile.TemporaryDirectory(prefix='reader-speech-') as directory:
            path=Path(directory)/'input.wav';path.write_bytes(binary)
            result=system({'action':'asr','input':str(path),'language':cfg['language']})
    else:
        # 语音接口不是统一的 OpenAI 形状：按平台分派（MiMo 走 /chat/completions 的 input_audio，
        # MiniMax 走 /speech_to_text）。详见 app/speech_protocols.py 里的核对说明。
        result=speech_protocols.transcribe(runtime('asr',cfg),binary,cfg['language'])
    text=result.get('text','')
    if not isinstance(text,str) or not text.strip():raise HTTPException(422,'未识别到清晰语音，请重试或改用文字')
    return {'text':text[:8000].strip(),'confidence':result.get('confidence'),'source':cfg['asr_mode']}

class SpeakBody(BaseModel):
    text:str=Field(min_length=1,max_length=12000)

class CorrectionFormatError(ValueError):
    """纠错回复里取不到可用的校正文本。"""

# 纠错只接受这几种明确的承载字段；模型换了别的键名就如实报告，而不是猜。
CORRECTION_TEXT_KEYS=('text','corrected_text','corrected')

def correction_payload(raw):
    """把纠错模型的回复解析成 (校正文本, needs_review)。

    此前这一步的失败全被折叠成一句"服务响应格式不兼容，请确认模型类型与接口协议正确"，
    用户完全无从下手（用户报告的原话）。实际上失败原因很具体：回复不是 JSON、
    JSON 里没有文本字段、文本是空的、或模型把内容包在说明文字/代码围栏里。
    这里逐一区分并抛出可读的原因。
    """
    text=(raw or '').strip()
    if not text:raise CorrectionFormatError('纠错模型返回了空内容')
    # 代码围栏与前后说明文字都很常见：先剥围栏，再退回"取出最外层 JSON 对象"。
    stripped=re.sub(r'^```(?:json)?\s*','',text);stripped=re.sub(r'\s*```$','',stripped).strip()
    payload=None
    for candidate in (stripped,):
        try:payload=json.loads(candidate)
        except ValueError:payload=None
        if payload is not None:break
    if payload is None:
        match=re.search(r'\{[\s\S]*\}',stripped)
        if match:
            try:payload=json.loads(match.group())
            except ValueError:payload=None
    if isinstance(payload,str):payload={'text':payload}
    if isinstance(payload,dict):
        for key in CORRECTION_TEXT_KEYS:
            value=payload.get(key)
            if isinstance(value,str) and value.strip():return value.strip(),payload.get('needs_review')
        raise CorrectionFormatError('纠错模型返回的 JSON 里没有可用的文本字段（期望 text，实际收到：'
                                    +'、'.join(sorted(str(k) for k in payload)[:6])+'）')
    raise CorrectionFormatError('纠错模型没有按要求返回 JSON（返回内容不是 JSON 对象，也没有可提取的 JSON 片段）')

def synthesize(text):
    cfg=config()
    if not cfg['output_enabled']:raise HTTPException(403,'语音播报未开启')
    if cfg['tts_mode']=='system':
        with tempfile.TemporaryDirectory(prefix='reader-speech-') as directory:
            output=Path(directory)/'output.wav';system({'action':'tts','text':text,'output':str(output),'voice':cfg['voice']})
            return output.read_bytes(),'audio/wav'
    return speech_protocols.synthesize(runtime('tts',cfg),text,cfg['voice'])
