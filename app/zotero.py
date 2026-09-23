"""Read-only Zotero local/cloud API and WebDAV attachment access; never writes upstream."""
import io, json, re, zipfile, base64
from pathlib import Path
from urllib.parse import urlsplit, unquote
from urllib.request import url2pathname
from typing import Literal
import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, SecretStr
from . import settings

class ZoteroSettings(BaseModel):
    mode: Literal['local','cloud','webdav']='local'
    library_type: Literal['users','groups']='users'
    library_id: str=Field(default='0',pattern=r'^\d{1,20}$')
    api_key: SecretStr=SecretStr('')
    clear_key: bool=False
    webdav_url: str=Field(default='https://dav.jianguoyun.com/dav/zotero',max_length=2048)
    webdav_user: str=Field(default='',max_length=300)
    webdav_password: SecretStr=SecretStr('')
    clear_password: bool=False

def config():
    with settings.storage() as conn:
        row=conn.execute("SELECT value FROM model_settings WHERE kind='zotero'").fetchone()
    default=ZoteroSettings().model_dump(exclude={'api_key','webdav_password','clear_key','clear_password'})
    return {**default,**(json.loads(row[0]) if row else {})}

def public():
    data=config()
    return {k:v for k,v in data.items() if k not in ('encrypted_key','encrypted_password')}|{'has_key':bool(data.get('encrypted_key')),'has_password':bool(data.get('encrypted_password'))}

def save(body):
    old=config();url=body.webdav_url.strip().rstrip('/')
    settings.validate_url(url)
    if urlsplit(url).scheme!='https': raise HTTPException(400,'WebDAV 密码连接必须使用 HTTPS')
    if body.mode!='local' and body.library_id=='0': raise HTTPException(400,'云端模式请填写 Zotero 用户或群组的数字 ID')
    if body.mode=='webdav' and body.library_type!='users': raise HTTPException(400,'Zotero WebDAV 附件仅支持个人资料库')
    same=old['library_id']==body.library_id and old['library_type']==body.library_type
    key='' if body.clear_key else body.api_key.get_secret_value().strip() or (settings.crypt(old.get('encrypted_key',''),True) if same else '')
    same_dav=old['webdav_url'].rstrip('/')==url and old['webdav_user']==body.webdav_user
    password='' if body.clear_password else body.webdav_password.get_secret_value() or (settings.crypt(old.get('encrypted_password',''),True) if same_dav else '')
    if len(key)>8192 or len(password)>8192: raise HTTPException(400,'密钥长度异常')
    data=body.model_dump(exclude={'api_key','webdav_password','clear_key','clear_password'});data['webdav_url']=url
    data.update(encrypted_key=settings.crypt(key),encrypted_password=settings.crypt(password))
    with settings.storage() as conn: conn.execute("INSERT OR REPLACE INTO model_settings VALUES('zotero',?)",(json.dumps(data),))
    return public()

def _mode_name(cfg):
    return {'local':'本机 Zotero','cloud':'Zotero 云端','webdav':'Zotero 云端 + WebDAV 附件'}.get(cfg['mode'],'Zotero')

def _read_failure(status,cfg):
    """按"连接方式 + HTTP 状态"给出可执行的说明。

    此前所有失败都折叠成同一句"请确认桌面已启动且允许本机 API，或检查云端 ID…"：
    云端模式下却让人去查桌面客户端，真正的原因（密钥权限、ID 不是数字、限流）全被这句
    话盖住——用户报告的"分类读取失败"正是这样拿不到线索。
    """
    if cfg['mode']=='local':
        if status==403:return 403,'本机 Zotero 拒绝了这次读取（HTTP 403）：请在 Zotero「设置 → 高级 → 其他」里勾选允许其他应用与 Zotero 通信。'
        if status==404:return 404,'本机 Zotero 里没有这个对象（HTTP 404）：个人资料库的数字 ID 应填 0。'
        return 502,f'本机 Zotero 返回 HTTP {status}，请查看 Zotero 自身的日志。'
    if status==403:return 403,'Zotero 云端拒绝了本次读取（HTTP 403）：API Key 无效或已被删除，或这把密钥没有资料库读取权限。请在 zotero.org 的 API Keys 页面确认已勾选个人资料库（或该群组）的读取权限，保存后重试。'
    if status==404:return 404,'Zotero 云端找不到这个资料库（HTTP 404）：这里要填的是数字用户 ID 或群组 ID，不是用户名、邮箱或群组名。可点「检查连接」，用 API Key 查出正确的数字 ID。'
    if status==412 or status==428:return 502,f'Zotero 云端要求重新校验库版本（HTTP {status}），请稍后重试。'
    if status==429:return 502,'Zotero 云端限流（HTTP 429）：请稍后重试，并减少同时进行的读取。'
    if status>=500:return 502,f'Zotero 云端暂时不可用（HTTP {status}），请稍后重试。'
    return 502,f'Zotero 云端返回 HTTP {status}。'

def _network_failure(cfg):
    if cfg['mode']=='local':
        return '连不上本机 Zotero（http://localhost:23119）：请确认桌面版 Zotero 已启动，并允许其他应用与它通信。'
    return '连不上 api.zotero.org：请检查网络、代理或防火墙设置。'

def _rebuild(response,status,content):
    """把已经读完的正文重新包成一个 Response 之前，先去掉与"已解码正文"冲突的响应头。

    这里踩过一个大坑：Zotero **云端**默认返回 `content-encoding: gzip`，而 `iter_bytes()`
    交出来的已经是解压后的字节。若原样把头带过去再构造 Response，httpx 会在构造时再解压
    一次，抛 `DecodingError: incorrect header check`。它发生在 try 之外，最终表现为
    **HTTP 500**（用户报告："zotero 云端储存和坚果云仍旧请求失败（HTTP 500）"）。
    本机 API 不压缩，所以同样的代码在本机一直正常——这就是"只有云端失败"的原因。
    """
    headers={k:v for k,v in response.headers.items()
             if k.lower() not in ('content-encoding','content-length','transfer-encoding')}
    return httpx.Response(status,headers=headers,content=bytes(content),request=response.request)

def api_get(path,params=None):
    cfg=config();local=cfg['mode']=='local'
    base='http://localhost:23119/api' if local else 'https://api.zotero.org'
    headers={'Zotero-API-Version':'3'}
    if not local:
        key=settings.crypt(cfg.get('encrypted_key',''),True)
        if not key: raise HTTPException(400,'请配置具有资料库与文件读取权限的 Zotero API Key')
        headers['Zotero-API-Key']=key
    limit=100*1024*1024 if path.endswith('/file') else 10*1024*1024
    try:
        with httpx.Client(timeout=30,trust_env=not local,follow_redirects=False) as client:
            with client.stream('GET',base+path,params=params,headers=headers) as response:
                status=response.status_code
                content=bytearray()
                if status<400:
                    for block in response.iter_bytes():
                        if len(content)+len(block)>limit: raise HTTPException(413,'Zotero 返回的数据超过大小上限，已中止。')
                        content.extend(block)
                result=_rebuild(response,status,content)
    except HTTPException: raise
    except httpx.TransportError:
        raise HTTPException(502,_network_failure(cfg)) from None
    except httpx.HTTPError as exc:
        # 解码/重定向之类的协议层错误：给出可复现的说法，而不是让它变成 500。
        raise HTTPException(502,f'读取 Zotero 的响应时出错（{type(exc).__name__}）。请重试；若持续出现，请检查代理或防火墙是否改写了响应。') from None
    if status>=400:
        code,detail=_read_failure(status,cfg)
        raise HTTPException(code,detail)
    return result

def key_info():
    """用当前保存的 API Key 查出它属于哪个账户、能读什么（只读 GET /keys/current）。

    密钥放在请求头里，不进 URL、不进日志。这是回答"云端分类为什么读不到"的关键一步：
    绝大多数情况是 ID 填成了用户名，或密钥没勾选资料库读取权限。
    """
    cfg=config()
    key=settings.crypt(cfg.get('encrypted_key',''),True)
    if not key: raise HTTPException(400,'还没有保存 Zotero API Key：云端与 WebDAV 附件模式都需要它。')
    headers={'Zotero-API-Version':'3','Zotero-API-Key':key}
    try:
        with httpx.Client(timeout=20,trust_env=True,follow_redirects=False) as client:
            r=client.get('https://api.zotero.org/keys/current',headers=headers)
    except httpx.HTTPError:
        raise HTTPException(502,_network_failure({**cfg,'mode':'cloud'})) from None
    if r.status_code>=400:
        code,detail=_read_failure(r.status_code,{**cfg,'mode':'cloud'})
        raise HTTPException(code,detail)
    payload=r.json()
    if not isinstance(payload,dict): raise HTTPException(502,'Zotero 返回的密钥信息不符合预期。')
    return payload

def diagnose():
    """逐步确认只读连接到底卡在哪一步，并给出下一步动作。

    只做只读 GET，不修改 Zotero 数据库、不上传任何内容。
    """
    cfg=config()
    result={'mode':cfg['mode'],'mode_name':_mode_name(cfg),'ok':False,'message':'','detail':[],'suggested_library_id':''}
    if cfg['mode']=='local':
        try:
            api_get(prefix()+'/collections',{'format':'json','limit':1})
            result.update(ok=True,message='本机 Zotero 可读：分类、条目与附件都从本机数据库读取。')
        except HTTPException as exc:
            result['message']=exc.detail
        return result
    try:
        payload=key_info()
    except HTTPException as exc:
        result['message']=exc.detail
        return result
    user_id=payload.get('userID');username=payload.get('username')
    access=payload.get('access') if isinstance(payload.get('access'),dict) else {}
    personal=access.get('user') if isinstance(access.get('user'),dict) else {}
    groups=access.get('groups') if isinstance(access.get('groups'),dict) else {}
    result['detail'].append(f'密钥属于账户 {username or "（平台未返回用户名）"}，数字用户 ID 为 {user_id}。')
    if cfg['library_type']=='users':
        if user_id and str(user_id)!=cfg['library_id']:
            result['suggested_library_id']=str(user_id)
            result['detail'].append('当前填写的用户 ID 与这把密钥所属的账户不一致：云端按 ID 取库，一定要填上面这个数字 ID。')
        if not personal.get('library'):
            result['detail'].append('这把密钥没有获得「个人资料库」读取权限（library=false），分类与条目都会被拒绝；请到 zotero.org 的 API Keys 页面重新勾选后再保存密钥。')
    else:
        entry=groups.get(str(cfg['library_id']))
        if not isinstance(entry,dict): result['detail'].append('这把密钥没有授权访问所填写的群组（group ID 也可能不对）。')
        elif not entry.get('library'): result['detail'].append('这把密钥对该群组没有读取权限。')
        else: result['detail'].append(f'这把密钥对群组 {cfg["library_id"]} 有读取权限。')
    if cfg['mode']=='webdav':
        if not cfg['webdav_user'] or not settings.crypt(cfg.get('encrypted_password',''),True):
            result['detail'].append('WebDAV 模式还缺账号或应用密码：不影响分类与条目读取，但打不开附件。')
    try:
        rows=api_get(prefix()+'/collections',{'format':'json','limit':1}).json()
        count=len(rows) if isinstance(rows,list) else 0
        result['ok']=True
        result['message']=f'连接可用：已成功读取分类（本次探测返回 {count} 条，最多 100 条一页）。'
    except HTTPException as exc:
        result['message']='读取分类失败：'+exc.detail
    return result

def prefix():
    cfg=config()
    return '/'+cfg['library_type']+'/'+cfg['library_id']

def collections():
    result=[];start=0
    while True:
        rows=api_get(prefix()+'/collections',{'format':'json','limit':100,'start':start}).json()
        result.extend({'key':r['key'],'name':r['data']['name'],'parent':r['data'].get('parentCollection') or ''} for r in rows)
        if len(rows)<100: break
        start+=100
    return {'collections':result}


def listing(query='',start=0,collection=''):
    if collection and not re.fullmatch('[A-Z0-9]{8}',collection): raise HTTPException(400,'分类标识无效')
    endpoint=prefix()+('/collections/'+collection if collection else '')+'/items/top'
    # q 是 Zotero 自己的快速搜索：默认 qmode=titleCreatorYear，**匹配标题、作者与年份**，
    # 不是只匹配标题；界面的输入框说明必须与之一致。本地 API 由 Zotero 的本地快速搜索实现，
    # 结果集合与云端可能略有差异（官方文档如此说明）。
    rows=api_get(endpoint,{'format':'json','limit':25,'start':start,'q':query}).json()
    def attachments(row):
        d=row.get('data',{});title=d.get('title') or d.get('filename') or row['key']
        children=[row] if d.get('itemType')=='attachment' else api_get(prefix()+'/items/'+row['key']+'/children',{'format':'json','limit':100}).json()
        result=[]
        for child in children:
            c=child.get('data',{})
            if c.get('contentType')!='application/pdf' and not c.get('filename','').lower().endswith('.pdf'): continue
            result.append({'key':child['key'],'title':title,'filename':c.get('filename',''),'attachment_title':c.get('title',''),'parent':c.get('parentItem','')})
        return result
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        result=[item for group in pool.map(attachments,rows) for item in group]
    return {'items':result,'next':start+25 if len(rows)==25 else None,'mode':config()['mode']}


def bounded_download(url,auth=None):
    if urlsplit(url).scheme!='https': raise HTTPException(400,'附件下载地址必须为 HTTPS')
    with httpx.Client(timeout=90,follow_redirects=False) as client:
        with client.stream('GET',url,auth=auth) as r:
            r.raise_for_status();out=io.BytesIO()
            for block in r.iter_bytes():
                if out.tell()+len(block)>100*1024*1024: raise HTTPException(413,'附件超过 100 MB')
                out.write(block)
            return out.getvalue()

def unzip_pdf(binary,filename):
    # Read one bounded member in memory; never extract archive paths to the filesystem.
    with zipfile.ZipFile(io.BytesIO(binary)) as archive:
        candidates=[]
        for info in archive.infolist():
            name=info.filename
            if name.endswith('%ZB64'):
                try:name=base64.b64decode(name[:-5]).decode('utf-8')
                except Exception:continue
            if name.lower().endswith('.pdf'): candidates.append((info,name))
        matches=[i for i,n in candidates if n==filename or n.replace('\\','/').split('/')[-1]==filename]
        if not matches and len(candidates)==1: matches=[candidates[0][0]]
        if len(matches)!=1: raise HTTPException(400,'WebDAV 压缩附件中无法唯一定位 PDF')
        info=matches[0]
        if info.file_size>100*1024*1024: raise HTTPException(413,'解压附件超过 100 MB')
        return archive.read(info)

def creators_author(data):
    """父条目 `data.creators` 里**标为作者**的那些人的姓名；没有就返回空串。

    这是 Zotero 条目本身记录的**事实性元数据**（用户自己的资料库），不涉及任何推断：
    不带 name 的项直接跳过，`creatorType` 不是 author 的（编者、译者、丛书编辑等）不算作者，
    因此不会把"编辑"冒充成作者——这与 PDF 侧的规则一致：读不到就留空，由用户自己填。

    姓名只做拼接（有姓与名就"名 姓"，否则用只有一个字段的 name），不翻译、不改写、
    不补全缩写、不猜机构名。条数很多时截断到 author 列的长度上限（200）。
    """
    names=[]
    for creator in (data.get('creators') or []):
        if not isinstance(creator,dict) or creator.get('creatorType')!='author': continue
        name=' '.join(part.strip() for part in (creator.get('firstName'),creator.get('lastName')) if isinstance(part,str) and part.strip())
        if not name:
            single=creator.get('name')
            name=single.strip() if isinstance(single,str) else ''
        if name: names.append(name)
    return '、'.join(names)[:200]

def attachment(item_key):
    if not re.fullmatch('[A-Z0-9]{8}',item_key): raise HTTPException(400,'Zotero 条目标识无效')
    cfg=config();r=api_get(prefix()+'/items/'+item_key);d=r.json()['data']
    if d.get('itemType')!='attachment' or d.get('contentType')!='application/pdf': raise HTTPException(400,'仅支持 PDF 附件')
    title=d.get('title') or d.get('filename') or item_key
    author=''
    if d.get('parentItem'):
        parent=api_get(prefix()+'/items/'+d['parentItem']).json().get('data',{})
        title=parent.get('title') or title
        author=creators_author(parent)
    identity=f"{cfg['mode']}:{cfg['library_type']}:{cfg['library_id']}:{r.headers.get('Zotero-Server-ID','legacy')}:{item_key}"
    if cfg['mode']=='local':
        response=api_get(prefix()+'/items/'+item_key+'/file')
        url=response.headers.get('location','');parsed=urlsplit(url)
        if parsed.scheme!='file' or parsed.netloc not in ('','localhost'): raise HTTPException(400,'附件尚未下载到本机，请先在 Zotero 打开附件完成同步')
        path=Path(url2pathname(parsed.path)).resolve()
        if path.suffix.lower()!='.pdf' or not path.is_file(): raise HTTPException(404,'本机 Zotero 附件不可用')
        if path.stat().st_size>100*1024*1024: raise HTTPException(413,'附件超过 100 MB')
        return {'title':title,'author':author,'identity':identity+':'+str(path),'path':path}
    try:
        if cfg['mode']=='webdav':
            password=settings.crypt(cfg.get('encrypted_password',''),True)
            if not cfg['webdav_user'] or not password: raise HTTPException(400,'请配置坚果云或 WebDAV 账号及应用密码')
            binary=bounded_download(cfg['webdav_url']+'/'+item_key+'.zip',auth=(cfg['webdav_user'],password))
            binary=unzip_pdf(binary,d.get('filename',''))
        else:
            response=api_get(prefix()+'/items/'+item_key+'/file')
            if response.status_code==302:
                # Signed storage URL receives no Zotero API key.
                binary=bounded_download(response.headers['location'])
            else: binary=response.content
        if not binary.startswith(b'%PDF-'): raise HTTPException(400,'返回内容不是 PDF；WebDAV 附件请选择对应模式')
        if len(binary)>100*1024*1024: raise HTTPException(413,'附件超过 100 MB')
        return {'title':title,'author':author,'identity':identity,'bytes':binary}
    except HTTPException: raise
    except Exception: raise HTTPException(502,'附件下载失败，请检查云端文件同步或 WebDAV 地址、应用密码与读取权限') from None
