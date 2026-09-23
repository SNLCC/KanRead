"""Cooperative cancellation with bounded request tombstones; no document content retained."""
import threading,time,functools
from contextvars import ContextVar
from fastapi import HTTPException
_lock=threading.RLock()
_jobs={}
_current=ContextVar('reader_job',default=None)

def cancel(key):
    with _lock:
        if key not in _jobs and len(_jobs)>=256:raise HTTPException(429,'请求过多')
        job=_jobs.setdefault(key,{'time':time.monotonic(),'cancelled':False,'clients':[]})
        job['cancelled']=True;clients=list(job['clients'])
    for client in clients:
        try: client.close()
        except Exception: pass
    return {'ok':True}

def check():
    job=_current.get()
    if job and job['cancelled']: raise HTTPException(499,'已停止本次回答')


def bind_documents(ids):
    job=_current.get()
    if job:
        with _lock:job['documents']=set(ids)


def cancel_document(doc_id):
    with _lock:keys=[key for key,job in _jobs.items() if job.get('running') and doc_id in job.get('documents',set())]
    for key in keys:cancel(key)


def progress(phase,current=0,total=0):
    check();job=_current.get()
    if job:
        with _lock:
            job['progress']={'phase':phase,'current':current,'total':total}
            job['time']=time.monotonic()


def draft(text,note='生成中：这一段还没有逐段核对',verified=False,report=None):
    """把**正在生成**的那段文字发布给轮询中的界面（只在内存里、请求结束即清）。

    为什么要它：一轮提问里最慢的是"形成分析"那一次生成，用户在它写完之前只能看着
    "分批阅读原文 · 3/4"。把这段文字边写边发出去，界面就能立刻显示，读完再被核对后的版本
    整体替换。它不是结论：界面必须标注"尚未核对"，最终结果到达时**整段替换**，
    所以这里只负责"让用户先看到"，不参与任何记账（台账、证据、引用都不读它）。

    `report` 是已完成的逐段核对记录（第 103 轮）：它只用于让"已核对"的那段在整轮结束前
    也能展开候选内容与原因——同样是显示用途，同样不进台账。
    """
    job=_current.get()
    if job:
        with _lock:
            job['draft']={'text':text,'note':note}
            if verified:job['draft']['verified']=True
            if report:job['draft']['report']=report
            job['time']=time.monotonic()


def clear_draft():
    job=_current.get()
    if job:
        with _lock:job.pop('draft',None)


def status(key):
    with _lock:
        job=_jobs.get(key,{})
        return {'running':job.get('running',False),'cancelled':job.get('cancelled',False),**job.get('progress',{}),
                'search_review':job.get('search_review'),'draft':job.get('draft')}


def review_search(plan):
    """Only the currently running request can wait for an explicit query approval."""
    import secrets
    job=_current.get()
    if not job:return False
    nonce=secrets.token_hex(16);event=threading.Event()
    with _lock:
        job['search_review']={**plan,'nonce':nonce};job['review_event']=event;job['approved']=False
    try:
        deadline=time.monotonic()+300
        while time.monotonic()<deadline:
            check()
            if event.wait(.2):return job['approved']
        return False
    finally:
        with _lock:job.pop('search_review',None);job.pop('review_event',None)


def approve_search(key,nonce,approved):
    with _lock:
        job=_jobs.get(key)
        if not job or not job.get('running') or job.get('search_review',{}).get('nonce')!=nonce:
            raise HTTPException(409,'搜索确认已过期')
        job['approved']=approved;job['review_event'].set()
    return {'ok':True}

def attach(client):
    check();job=_current.get()
    if job:
        with _lock: job['clients'].append(client)
    return client

def cancellable(fn):
    @functools.wraps(fn)
    def wrapped(body,*args,**kwargs):
        key=getattr(body,'request_id','')
        if not key:return fn(body,*args,**kwargs)
        with _lock:
            now=time.monotonic()
            for old in list(_jobs):
                if not _jobs[old].get('running') and now-_jobs[old]['time']>600: _jobs.pop(old)
            if key not in _jobs and len(_jobs)>=256:raise HTTPException(429,'请求过多，请稍后重试')
            job=_jobs.setdefault(key,{'time':now,'cancelled':False,'clients':[]})
            if job.get('running'):raise HTTPException(409,'请求正在执行')
            job['running']=True
        token=_current.set(job)
        try:check();return fn(body,*args,**kwargs)
        finally:
            _current.reset(token)
            with _lock:
                clients=job['clients'];job['clients']=[]
                job['running']=False
                # 撤销"边生成边显示"的那段文字：它只在这一次请求存活期间有意义，
                # 留着就等于把没核对过的文本留在内存里（本模块的承诺是不保留内容）。
                job.pop('draft',None)
            # 请求结束就关掉本次作业用过的客户端（包括中途重建的那些）：
            # 不关的话每个请求都会漏一个连接池，长期运行会越积越多。
            for client in clients:
                try: client.close()
                except Exception: pass
    return wrapped


def begin_request(key):
    """后台线程自己建立一个可取消的请求上下文（整篇翻译就是这种任务）。

    与 ``cancellable`` 同一套作业表：因此界面上的"停止"和对话里的"停止"走同一条路，
    不需要为后台任务另写一套取消机制。返回的 token 必须交给 ``end_request``。
    """
    with _lock:
        now=time.monotonic()
        for old in list(_jobs):
            if not _jobs[old].get('running') and now-_jobs[old]['time']>600:_jobs.pop(old)
        job=_jobs.setdefault(key,{'time':now,'cancelled':False,'clients':[]})
        job['running']=True
    return _current.set(job)


def end_request(token):
    job=_current.get()
    _current.reset(token)
    if job:
        with _lock:
            clients=job['clients'];job['clients']=[]
            job['running']=False
            # 与 `cancellable` 同一条：后台任务结束时也把"边生成边显示"的那段文字撤掉。
            job.pop('draft',None)
        for client in clients:
            try: client.close()
            except Exception: pass
