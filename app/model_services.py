"""Protocol adapters shared by live requests and configuration probes."""
import json
import math
import ipaddress
from urllib.parse import urlsplit
import httpx
from fastapi import HTTPException
from . import settings


def headers(profile):
    return {'Authorization':'Bearer '+(profile['api_key'] or 'local')}


def use_proxy(profile):
    host=urlsplit(profile['base_url']).hostname or ''
    if host=='localhost' or host.endswith('.local'):
        return False
    try:
        ip=ipaddress.ip_address(host)
        return not (ip.is_private or ip.is_loopback)
    except ValueError:
        return True


class ResponseFormatError(ValueError):
    pass


def response_text(data):
    """Never substitute hidden reasoning for the answer."""
    if not isinstance(data,dict):raise ResponseFormatError('服务未返回 JSON 对象。')
    rows=data.get('choices')
    if not isinstance(rows,list) or not rows:raise ResponseFormatError('服务未返回 choices；请选择兼容 Chat Completions 的会话接口。')
    row=rows[0];message=row.get('message') or {};content=message.get('content')
    if isinstance(content,list):content=''.join(p.get('text','') for p in content if isinstance(p,dict) and p.get('type') in ('text','output_text'))
    if row.get('finish_reason')=='length':raise ResponseFormatError('模型达到输出上限，正文或结构化结果可能被截断；请提高输出预留，或使用非推理模式。')
    if message.get('refusal'):raise ResponseFormatError('模型拒绝处理本次请求。')
    if not isinstance(content,str) or not content.strip():
        raise ResponseFormatError('模型仅返回推理或空正文；请增加输出预留或切换非推理模式。' if message.get('reasoning_content') else '模型没有返回可显示的正文。')
    return content.strip()


def stream_deltas(lines,on_delta=None):
    """消费 Chat Completions 的流式响应，返回 ``(正文, 用量)``。

    只认标准协议里的两件事：``data: {...}`` 行中 ``choices[0].delta.content`` 的文本增量，
    以及夹带的 ``usage``。注释行、空行、``[DONE]``、以及厂商自造的字段一律忽略——
    **不猜私有格式**（与 :func:`task_options` 同一条纪律：猜错会把无关平台打挂）。

    ``on_delta(piece, reset=False)``：每拿到一段文本就回调一次；``reset=True`` 表示
    "此前发出去的那段作废，从头来"（流式中途失败、退回普通请求时会用到）。
    """
    parts=[];usage=None
    for raw in lines:
        line=(raw or '').strip()
        if not line or line.startswith(':'):continue
        if line.startswith('data:'):line=line[5:].strip()
        if line=='[DONE]':break
        try:chunk=json.loads(line)
        except ValueError:continue
        if not isinstance(chunk,dict):continue
        if isinstance(chunk.get('usage'),dict):usage=chunk['usage']
        for row in chunk.get('choices') or []:
            piece=((row or {}).get('delta') or {}).get('content')
            if isinstance(piece,list):
                piece=''.join(p.get('text','') for p in piece if isinstance(p,dict) and p.get('type') in ('text','output_text'))
            if isinstance(piece,str) and piece:
                parts.append(piece)
                if on_delta:on_delta(piece)
    return ''.join(parts),usage


def task_options(profile,task=False):
    """每次调用要带的额外参数。

    现在的两件事，**都只作用于用户自己配置的这个服务地址**：

    1. DeepSeek：官方协议下任务型调用关闭思考（第 59 轮起就在做）；
    2. 其它平台：只有当用户在设置里**明确打开**「任务型调用关闭深度思考」时才发
       `thinking={'type':'disabled'}`，且同样只限任务型调用。

    为什么默认不发：厂商私有参数名各不相同，猜着发会把无关 endpoint 打挂（返回 400 或直接报错）。
    这条纪律从第 59 轮起就写在这里，`task_thinking` 没有违反它——参数是用户自己开的，
    对象是他自己填的地址，作用范围也只有任务型调用（最终回答那一次仍然保留思考）。

    为什么值得开：实测一份 8 页文献的一轮提问会做 11 次调用、输出 18,547 token，
    其中**思考 token 占 69%**；而这些调用做的是"读这一批、合并、核对"这类确定性工作，
    思考的边际收益远小于它的耗时。
    """
    from urllib.parse import urlsplit
    options={}
    if urlsplit(profile.get('base_url','')).hostname=='api.deepseek.com':
        options['thinking']={'type':'disabled' if task else 'enabled'}
    elif task and profile.get('task_thinking'):
        options['thinking']={'type':'disabled'}
    return options


def service_error(exc):
    if isinstance(exc,ResponseFormatError):return str(exc)
    # Never relay upstream response bodies: providers may echo keys or input.
    if isinstance(exc,httpx.TimeoutException):
        return '连接超时。本地模型首次加载可能较慢，请确认模型已加载后重试。'
    if isinstance(exc,httpx.ConnectError):
        return '无法连接服务。请检查地址，并确认本地模型服务已启动。'
    if isinstance(exc,httpx.HTTPStatusError):
        status=exc.response.status_code
        return {401:'API Key 无效，请重新填写。',403:'此密钥没有访问权限，请检查平台授权。',
                404:'接口或模型不存在，请检查服务根地址和模型名称。',429:'请求受限或额度不足，请检查平台额度后重试。'}.get(status,f'模型服务返回 HTTP {status}，请稍后重试或检查服务配置。')
    # 兜底也要带上真实原因：只说"格式不兼容"会把"代码里有 bug""返回的不是 JSON"这类
    # 完全不同的情况混成一句无用的提示（翻译协议那边就是这么被误导过一次）。
    detail=str(exc).strip().splitlines()[0][:200] if str(exc).strip() else exc.__class__.__name__
    return f'服务响应无法处理（{exc.__class__.__name__}：{detail}）。请确认接口协议与模型类型正确。'


def rerank(query,chunks,profile):
    if not chunks:
        return []
    with httpx.Client(timeout=90,trust_env=use_proxy(profile)) as client:
        r=client.post(profile['base_url']+'/rerank',headers=headers(profile),json={
            'model':profile['model'],'query':query,'documents':[c['text'] for c in chunks],
            'top_n':min(8,len(chunks)),'return_documents':False})
        r.raise_for_status()
    rows=r.json()['results']
    if not rows or not isinstance(rows,list):
        raise ValueError('Invalid ranking')
    seen=set()
    result=[]
    for row in rows:
        i=row['index']; score=row['relevance_score']
        if type(i) is not int or i<0 or i>=len(chunks) or i in seen or not isinstance(score,(int,float)) or not math.isfinite(score):
            raise ValueError('Invalid ranking index or score')
        seen.add(i)
        result.append({**chunks[i],'score':score})
    return sorted(result,key=lambda row:row['score'],reverse=True)[:8]


def probe(body, models=False):
    body=body.model_copy(update={'enabled':True})
    profile=settings.resolve(body.kind,body,require_model=not models)
    try:
        with httpx.Client(timeout=httpx.Timeout(60,connect=5),trust_env=use_proxy(profile)) as client:
            if models:
                params={'sub_type':{'chat':'chat','embedding':'embedding','rerank':'reranker'}[body.kind]} if profile['provider']=='siliconflow' else {}
                r=client.get(profile['base_url']+'/models',headers=headers(profile),params=params)
                r.raise_for_status()
                rows=r.json()['data']
                ids=sorted(set(row['id'] for row in rows if isinstance(row.get('id'),str)))
                return {'models':ids,'message':f'获取到 {len(ids)} 个模型。请按用途选择，并测试连接确认。'}
            if body.kind=='chat':
                r=client.post(profile['base_url']+'/chat/completions',headers=headers(profile),json={
                    'model':profile['model'],'messages':[{'role':'user','content':'Reply with OK.'}],'max_tokens':64})
                r.raise_for_status()
                text=r.json()['choices'][0]['message']['content']
                if not isinstance(text,str) or not text.strip():
                    raise ValueError('Empty chat response')
                detail='已收到会话模型回复'
            elif body.kind=='embedding':
                r=client.post(profile['base_url']+'/embeddings',headers=headers(profile),json={
                    'model':profile['model'],'input':['Connection test']})
                r.raise_for_status()
                vector=r.json()['data'][0]['embedding']
                if not vector or not all(isinstance(v,(int,float)) and math.isfinite(v) for v in vector):
                    raise ValueError('Invalid embedding')
                detail=f'已收到 {len(vector)} 维向量'
            else:
                rerank('reading',[{'text':'Reading a book'},{'text':'Cooking a meal'}],profile)
                detail='已收到重排序结果'
        return {'ok':True,'message':detail+'；点击保存后生效。'}
    except Exception as exc:
        raise HTTPException(502,service_error(exc)) from None
