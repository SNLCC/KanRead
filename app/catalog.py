"""Reusable platform connections. Keys are stored once, encrypted by Windows DPAPI."""
import json,re,uuid
import httpx
from pydantic import BaseModel,Field,SecretStr
from fastapi import HTTPException
from . import settings,model_services

KINDS={'chat','embedding','rerank','asr','tts','image_input','video_input'}
class Connection(BaseModel):
    name:str=Field(min_length=1,max_length=100)
    provider:str=Field(default='custom',max_length=40)
    base_url:str=Field(max_length=2048)
    api_key:SecretStr=SecretStr('')
    clear_key:bool=False
class ModelChoice(BaseModel):
    model:str=Field(min_length=1,max_length=300)
    capabilities:list[str]=Field(default_factory=list,max_length=7)
class Assignment(BaseModel):
    connection_id:str
    model:str=Field(min_length=1,max_length=300)
    voice:str=Field(default='',max_length=200)

def storage():
    c=settings.storage();c.execute('CREATE TABLE IF NOT EXISTS platform_connections(id TEXT PRIMARY KEY,value TEXT NOT NULL)');return c

def get(cid):
    with storage() as c:row=c.execute('SELECT value FROM platform_connections WHERE id=?',(cid,)).fetchone()
    if not row:raise HTTPException(404,'平台连接不存在，请重新选择')
    return json.loads(row[0])

def public_item(cid,data):return {k:v for k,v in data.items() if k!='encrypted_key'}|{'id':cid,'has_key':bool(data.get('encrypted_key'))}


def models_of(data):
    """这条连接的完整模型清单 = 平台返回/用户标注的模型 + 官方文档登记的语音模型。

    用户报告的第 2 项：MiniMax 平台有语音模型，但"获取模型"里没有。平台自己的
    `/v1/models` 只列对话模型，所以只按它建清单就永远看不到 TTS/ASR 模型。
    官方快照（app/model_metadata.py）按主机名补充，并如实标注来源；
    **用户标注过的模型以用户为准**——那是用户自己核实过的结论。
    """
    from .model_metadata import speech_models
    rows={m['id']:dict(m) for m in data.get('models',[]) if isinstance(m,dict) and m.get('id')}
    for preset in speech_models(data.get('base_url','')):
        current=rows.get(preset['id'])
        if current is None:rows[preset['id']]=preset
        elif current.get('source')!='用户标注':rows[preset['id']]={**current,**preset}
    return list(rows.values())


def listing():
    with storage() as c:
        rows=[(r[0],json.loads(r[1])) for r in c.execute('SELECT id,value FROM platform_connections ORDER BY rowid')]
    return [public_item(cid,{**data,'models':models_of(data)}) for cid,data in rows]

def save(body,cid=None):
    if not body.name.strip():raise HTTPException(400,'请填写平台连接名称')
    old=get(cid) if cid else {};cid=cid or uuid.uuid4().hex
    url=body.base_url.strip().rstrip('/');settings.validate_url(url)
    key=body.api_key.get_secret_value().strip()
    if len(key)>8192:raise HTTPException(400,'密钥过长')
    value={'name':body.name.strip(),'provider':body.provider,'base_url':url,'models':old.get('models',[]) if old.get('base_url')==url else [],'encrypted_key':settings.crypt(key) if key and not body.clear_key else old.get('encrypted_key','') if not body.clear_key and old.get('base_url')==url else ''}
    with storage() as c:c.execute('INSERT OR REPLACE INTO platform_connections VALUES(?,?)',(cid,json.dumps(value,ensure_ascii=False)))
    return public_item(cid,value)

def runtime(cid):
    row=get(cid)
    return {'provider':row['provider'],'base_url':row['base_url'],'api_key':settings.crypt(row.get('encrypted_key',''),True)}


def _stored(kind):
    """直接读一条用途配置，不做解密、不套用环境变量。

    删除平台时只需要判断"谁引用了这个连接"，因此不能走 get_profile()：
    它会在密钥无法解密时抛错（删除操作不该被一把失效密钥挡住），
    也会把环境变量兜底出来的配置误当成本机保存的引用。
    """
    with settings.storage() as c:row=c.execute('SELECT value FROM model_settings WHERE kind=?',(kind,)).fetchone()
    try:return json.loads(row[0]) if row else None
    except ValueError:return None


def impact(cid):
    """列出引用该连接的用途，并给出删除后各自的去向（供界面明确告知用户）。"""
    affected=[]
    for kind,label in (('chat','会话模型'),('embedding','Embedding'),('rerank','Rerank')):
        data=_stored(kind)
        if data and data.get('connection_id')==cid:affected.append({'kind':kind,'label':label,'result':'改为未配置'})
    speech=_stored('speech')
    if speech:
        for kind,label in (('asr','语音识别'),('tts','语音播报')):
            if speech.get(kind+'_connection_id')==cid:affected.append({'kind':'speech:'+kind,'label':label,'result':'改回本机 Windows'})
    parsing=_stored('parsing')
    if parsing and parsing.get('connection_id')==cid:affected.append({'kind':'parsing','label':'PDF 与文字识别','result':'改为独立服务地址或本机 OCR'})
    return affected


def unbind(cid,data):
    """删除连接前解除引用：能原样保留的设置保留，无法保留的明确降级。"""
    done=[]
    with settings.storage() as c:
        for kind,label in (('chat','会话模型'),('embedding','Embedding'),('rerank','Rerank')):
            stored=_stored(kind)
            if not stored or stored.get('connection_id')!=cid:continue
            # 保留用户已确认的阅读容量与高级选项，只清掉指向已删除平台的地址与密钥。
            stored.update({'enabled':False,'provider':'custom','base_url':'','model':'','connection_id':'','encrypted_key':''})
            c.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)',(kind,json.dumps(stored,ensure_ascii=False)))
            done.append({'kind':kind,'label':label,'result':'已改为未配置，需要重新在模型服务选择模型'})
        speech=_stored('speech')
        if speech:
            changed=False
            for kind,label in (('asr','语音识别'),('tts','语音播报')):
                if speech.get(kind+'_connection_id')!=cid:continue
                speech.update({kind+'_connection_id':'',kind+'_mode':'system',kind+'_url':'',kind+'_model':'','encrypted_'+kind:''})
                changed=True
                done.append({'kind':'speech:'+kind,'label':label,'result':'已改回本机 Windows，可在语音设置重新选择'})
            if changed:c.execute("INSERT OR REPLACE INTO model_settings VALUES('speech',?)",(json.dumps(speech,ensure_ascii=False),))
        parsing=_stored('parsing')
        if parsing and parsing.get('connection_id')==cid:
            # 识别服务本来就常常是一个独立的 MinerU / 解析服务地址：把地址保留下来，
            # 用户不会因为"顺手删掉一个平台连接"而丢掉已经能用的识别配置。
            parsing['service_url']=parsing.get('service_url') or data.get('base_url','')
            parsing['connection_id']=''
            if parsing.get('mode')=='vision':
                parsing.update({'mode':'local','model':''})
                done.append({'kind':'parsing','label':'PDF 与文字识别','result':'图像模型来自该平台，已改回本机 OCR'})
            else:
                done.append({'kind':'parsing','label':'PDF 与文字识别','result':'已改为独立服务地址，请核对'})
            c.execute("INSERT OR REPLACE INTO model_settings VALUES('parsing',?)",(json.dumps(parsing,ensure_ascii=False),))
    return done


def remove(cid):
    data=get(cid)
    unbound=unbind(cid,data)
    with storage() as c:c.execute('DELETE FROM platform_connections WHERE id=?',(cid,))
    return {'ok':True,'name':data.get('name',''),'unbound':unbound,
            'message':'平台连接与保存的密钥已删除；原始文献、云端附件与平台账户不受影响。'}

def classify(row):
    model=row['id'];caps=set();source='未确认'
    raw=row.get('type') or row.get('sub_type')
    mapping={'chat':'chat','embedding':'embedding','reranker':'rerank','rerank':'rerank','asr':'asr','tts':'tts'}
    if isinstance(raw,str) and raw in mapping:caps.add(mapping[raw]);source='平台元数据'
    architecture=row.get('architecture') if isinstance(row.get('architecture'),dict) else {}
    modalities=row.get('input_modalities',architecture.get('input_modalities',[]))
    output_modalities=row.get('output_modalities',architecture.get('output_modalities',[]))
    if isinstance(modalities,list) and isinstance(output_modalities,list):
        if 'text' in modalities and 'text' in output_modalities:caps.add('chat');source='平台元数据'
    if isinstance(modalities,list):
        if 'image' in modalities:caps.add('image_input');caps.add('chat');source='平台元数据'
        if 'video' in modalities:caps.add('video_input');caps.add('chat');source='平台元数据'
    if not caps:
        name=model.lower()
        # 语音模型的命名差异很大：MiMo 用 asr / tts 后缀，MiniMax 用 speech-02-hd（TTS）与
        # asr-1.0。平台不返回语音模型时连名字都见不到（见 models_of），这里只处理
        # "平台确实返回了这一行，但没给类型元数据"的情况。TTS/ASR 规则必须排在对话规则
        # 之前，否则 mimo-v2.5-tts 会先被 mimo 这条命中、被当成对话模型。
        for pattern,kind in [('embed|bge-m3','embedding'),('rerank','rerank'),
                             ('whisper|sensevoice|telespeech|asr|speech[_-]?to[_-]?text','asr'),
                             ('cosyvoice|orpheus|tts\\b|t2a|speech[-_]\\d','tts'),
                             ('deepseek|mimo|kimi|glm|qwen|llama|minimax','chat')]:
            if re.search(pattern,name):caps.add(kind);source='名称推测，需核实';break
    context=row.get('context_length') or row.get('context_window') or row.get('max_model_len')
    metadata={'context_window':context} if type(context) is int and 8192<=context<=4000000 else {}
    return {'id':model,'capabilities':sorted(caps),'source':source,**metadata}

def refresh(cid):
    data=get(cid);profile=runtime(cid)
    from .model_metadata import official
    rows=None;failure=None
    try:
        with httpx.Client(timeout=30,trust_env=model_services.use_proxy(profile)) as client:
            r=client.get(profile['base_url']+'/models',headers=model_services.headers(profile));r.raise_for_status();rows=r.json()['data']
        if not isinstance(rows,list) or len(rows)>10000:raise ValueError()
    except Exception as exc:
        # 平台没有这个接口、或这次调用失败时，只要官方文档登记过它的语音模型，就仍然
        # 把能确定的模型列出来并说明"实时列表没拿到"，而不是只丢一个失败提示、让用户
        # 以为这个平台没有可用的语音模型（用户报告的第 2 项正是这种"自动获取有缺陷"）。
        rows=None;failure=exc
    presets=models_of({**data,'models':[]})
    if rows is None:
        if not presets:raise HTTPException(502,'模型列表获取失败；平台连接已保留，可手动添加模型。'+model_services.service_error(failure)) from None
        data['models']=models_of(data)
        with storage() as c:c.execute('UPDATE platform_connections SET value=? WHERE id=?',(json.dumps(data,ensure_ascii=False),cid))
        return {**public_item(cid,data),'notice':'实时模型列表没有取到（'+model_services.service_error(failure)+'）'
                                          '；当前显示的是已保存的模型与官方文档登记的语音模型，可手动添加其它模型。'}
    previous={m['id']:m for m in data.get('models',[]) if isinstance(m,dict) and m.get('id') and m.get('source')=='用户标注'}
    models={row['id']:{**classify(row),**official(profile['base_url'],row['id']),**(previous.get(row['id']) or {})} for row in rows if isinstance(row,dict) and isinstance(row.get('id'),str) and 0<len(row['id'])<=300}
    if data['provider']=='ollama':
        root=profile['base_url'].removesuffix('/v1')
        with httpx.Client(timeout=30,trust_env=model_services.use_proxy(profile)) as client:
            for name,item in models.items():
                try:
                    r=client.post(root+'/api/show',headers=model_services.headers(profile),json={'model':name});r.raise_for_status();detail=r.json()
                    caps=detail.get('capabilities',[]);mapping={'completion':'chat','embedding':'embedding','vision':'image_input'}
                    item['capabilities']=[mapping[c] for c in caps if c in mapping];item['source']='Ollama 本机元数据'
                    for k,v in detail.get('model_info',{}).items():
                        if k.endswith('.context_length') and type(v) is int and 8192<=v<=4000000:item['model_context_max']=v
                except Exception:pass
    if data['provider']=='ollama':
        try:
            with httpx.Client(timeout=15,trust_env=model_services.use_proxy(profile)) as client:
                r=client.get(profile['base_url'].removesuffix('/v1')+'/api/ps',headers=model_services.headers(profile));r.raise_for_status()
                for row in r.json().get('models',[]):
                    name=row.get('name');capacity=row.get('context_length')
                    if name in models and type(capacity) is int and 8192<=capacity<=4000000:models[name]['context_window']=capacity
        except Exception:pass
    # 平台列表里没有、但官方文档登记过的语音模型：补进来（MiniMax 的 TTS、MiMo 的 ASR/TTS）。
    data['models']=models_of({**data,'models':list(models.values())})
    with storage() as c:c.execute('UPDATE platform_connections SET value=? WHERE id=?',(json.dumps(data,ensure_ascii=False),cid))
    return public_item(cid,data)

def mark(cid,body):
    if not set(body.capabilities)<=KINDS:raise HTTPException(400,'模型能力类型无效')
    data=get(cid);known=next((m for m in models_of(data) if m['id']==body.model),{})
    data['models']=[m for m in data.get('models',[]) if m['id']!=body.model]+[{**known,'id':body.model,'capabilities':body.capabilities,'source':'用户标注'}]
    with storage() as c:c.execute('UPDATE platform_connections SET value=? WHERE id=?',(json.dumps(data,ensure_ascii=False),cid))
    return public_item(cid,{**data,'models':models_of(data)})

def assign(kind,body):
    if kind not in ('chat','embedding','rerank','asr','tts'):raise HTTPException(400,'用途无效')
    data=get(body.connection_id);model=next((m for m in models_of(data) if m['id']==body.model),None)
    if not model or kind not in model['capabilities']:raise HTTPException(400,'请先确认并标注此模型支持该用途')
    if kind in ('asr','tts'):
        from . import speech
        cfg=speech.config();cfg[kind+'_connection_id']=body.connection_id;cfg[kind+'_mode']='custom';cfg[kind+'_url']=data['base_url'];cfg[kind+'_model']=body.model;cfg['encrypted_'+kind]=''
        if kind=='tts':cfg['voice']=body.voice
        with settings.storage() as c:c.execute("INSERT OR REPLACE INTO model_settings VALUES('speech',?)",(json.dumps(cfg),))
    else:
        old=settings.get_profile(kind)
        same=old['model']==body.model and old.get('connection_id')==body.connection_id
        cfg={'enabled':True,'provider':'custom','base_url':data['base_url'],'model':body.model,'connection_id':body.connection_id,'encrypted_key':'',
             'capacity_mode':old.get('capacity_mode','auto') if same else 'auto',
             'context_window':old.get('context_window',65536) if same else model.get('context_window',65536),
             'output_reserve':old.get('output_reserve',4096),'max_read_batches':old.get('max_read_batches',64),
             'tokenizer_id':old.get('tokenizer_id','') if same else '', 'visual_reading':old.get('visual_reading',False) if same else False}
        cfg['output_reserve']=min(cfg['output_reserve'],cfg['context_window']//2)
        with settings.storage() as c:c.execute('INSERT OR REPLACE INTO model_settings VALUES(?,?)',(kind,json.dumps(cfg)))
    return {'ok':True,'message':'已选择模型；语音输入与自动播报开关保持原状态。'}
