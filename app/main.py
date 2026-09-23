import json
import os
import sqlite3
import uuid
from pathlib import Path
from functools import lru_cache
from contextlib import contextmanager

import httpx
from . import pdf_engine
from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from typing import Literal

from .retrieval import rank
from . import embeddings, settings, model_services
from . import web_search, zotero, cancellation, speech
from .plugins import builtins
features = builtins()

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("READER_DATA_DIR", ROOT / "data"))
DATA.mkdir(parents=True, exist_ok=True)
app = FastAPI(title="勘读 · KanRead")

# 接口版本。改动了前端依赖的后端行为（新增接口、改变返回结构）就要 +1。
# 前端启动时会拿它和自带的要求比对：不一致说明"磁盘上的程序更新了，但正在跑的后台仍是旧版本"，
# 于是明确提示重启，而不是让用户对着旧行为反复排查（本项目改的是后端，只刷新页面不够）。
from .version import API_VERSION


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse(status_code=422,content={'detail':'输入格式不正确，请检查填写内容。'})


@contextmanager
def db():
    connection = sqlite3.connect(DATA / "reader.sqlite3", timeout=30)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    finally:
        connection.close()


with db() as c:
    c.executescript("""
    CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, name TEXT, pages INTEGER, current_page INTEGER DEFAULT 1, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT, page INTEGER, text TEXT, bbox TEXT);
    CREATE TABLE IF NOT EXISTS notes(id TEXT PRIMARY KEY, document_id TEXT, page INTEGER, quote TEXT, content TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS messages(id INTEGER PRIMARY KEY, document_id TEXT, role TEXT, content TEXT, sources TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE INDEX IF NOT EXISTS chunk_doc ON chunks(document_id, page);
    """)

    if 'bbox_space' not in {r[1] for r in c.execute('PRAGMA table_info(chunks)')}:
        c.execute("ALTER TABLE chunks ADD COLUMN bbox_space TEXT DEFAULT 'legacy'")
    if 'deleted' not in {r[1] for r in c.execute('PRAGMA table_info(documents)')}:
        c.execute('ALTER TABLE documents ADD COLUMN deleted INTEGER DEFAULT 0')
    if 'metadata' not in {r[1] for r in c.execute('PRAGMA table_info(messages)')}:
        c.execute("ALTER TABLE messages ADD COLUMN metadata TEXT DEFAULT '{}'")
    c.execute('CREATE TABLE IF NOT EXISTS annotations(id TEXT PRIMARY KEY, document_id TEXT, page INTEGER, quote TEXT, content TEXT, rects TEXT, color TEXT, deleted INTEGER DEFAULT 0)')
    if 'style' not in {r[1] for r in c.execute('PRAGMA table_info(annotations)')}:c.execute("ALTER TABLE annotations ADD COLUMN style TEXT DEFAULT 'highlight'")
    # 在译文栏里选中的那一小段译文（用户要求"可以对其中部分内容批注"）：锚点仍是原文，
    # 这个字段只用来把标记精确画在译文的那一段文字上。老库自动补列，老批注留空即可。
    if 'translation_excerpt' not in {r[1] for r in c.execute('PRAGMA table_info(annotations)')}:
        c.execute("ALTER TABLE annotations ADD COLUMN translation_excerpt TEXT DEFAULT ''")
    if 'deleted' not in {r[1] for r in c.execute('PRAGMA table_info(notes)')}:c.execute('ALTER TABLE notes ADD COLUMN deleted INTEGER DEFAULT 0')

    columns={r[1] for r in c.execute('PRAGMA table_info(documents)')}
    for name,definition in [('storage_kind',"TEXT DEFAULT 'copy'"),('external_path',"TEXT DEFAULT ''"),('external_key',"TEXT DEFAULT ''"),('file_stamp',"TEXT DEFAULT ''"),('tags',"TEXT DEFAULT '[]'")]:
        if name not in columns: c.execute(f'ALTER TABLE documents ADD COLUMN {name} {definition}')
    # 作者：PDF 元数据里有就自动填，没有就留空由用户自己写。检索的"名称、标签或作者"要用到它。
    if 'author' not in columns:
        c.execute("ALTER TABLE documents ADD COLUMN author TEXT DEFAULT ''")

# 译文单独建表：chunks 是原文索引，引用核验、阅读与批注都依赖它只含原文，
# 因此译文不写进去，而是由 translation.search_chunks() 作为派生行参与检索。
from .translation import ensure_tables as _ensure_translation_tables
with db() as c:
    _ensure_translation_tables(c)


@app.middleware("http")
async def local_only(request: Request, call_next):
    # Prevent arbitrary websites from mutating this loopback service.
    host = request.headers.get("host", "").split(":")[0]
    if host not in ("localhost", "127.0.0.1", "testserver"):
        return Response("Invalid host", 403)
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        return Response("Invalid origin", 403)
    return await call_next(request)


def document(doc_id):
    with db() as c:
        row = c.execute("SELECT * FROM documents WHERE id=? AND deleted=0", (doc_id,)).fetchone()
    if not row:
        raise HTTPException(404, "文档不存在")
    return dict(row) | {'tags':json.loads(row['tags'])}


def page_document(doc_id, page):
    meta = document(doc_id)
    if page < 1 or page > meta["pages"]:
        raise HTTPException(400, "页码超出范围")
    return pdf_engine.open_pdf(document_path(meta))


def document_path(meta):
    path=Path(meta['external_path']) if meta.get('storage_kind')=='zotero' else DATA/f"{meta['id']}.pdf"
    if not path.is_file(): raise HTTPException(404,'附件不可用；Zotero 文件可能已移动，请在 Zotero 页面重新连接此附件。')
    if meta.get('storage_kind')=='zotero' and meta.get('file_stamp')!=f'{path.stat().st_size}:{path.stat().st_mtime_ns}':
        raise HTTPException(409,'Zotero 附件已改变，请重新连接以更新索引。原有批注保留，但位置需核对。')
    return path


@app.get("/api/status")
def status():
    chat_config = settings.get_profile('chat')
    return {"ai": chat_config['enabled'], "model": chat_config['model'], "rerank":settings.get_profile('rerank')['enabled'],
            "api_version": API_VERSION,
            # 静态资源的当前版本号（按文件修改时间生成，与 index.html 注入的 __ASSET_VERSION 同源）。
            # 界面自己拿它比对：不一致说明浏览器还在跑缓存的旧脚本，"改了却没生效"要能当场看出来。
            "asset_version": asset_version(),
            "retrieval": "语义嵌入 + 词项混合检索" if embeddings.enabled() else "本地词项向量检索", "embedding": embeddings.enabled(), "ocr": True, "ocr_engine":"RapidOCR / PaddleOCR (local)"}


@app.get('/api/health')
def health():
    # `closing`：后台是否即将因为"没有打开的阅读窗口"而退出。启动器据此决定复用还是等它退出，
    # 避免用户刚关掉窗口又立刻打开时，拿到一个几秒后就自行退出的后台。
    from . import session_guard
    return {'application':'kanread', 'version':2, 'api_version':API_VERSION,
            'closing':session_guard.closing()}


class PurgeConfirmation(BaseModel):
    confirm_name: str


class DocumentName(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class DocumentAuthor(BaseModel):
    """作者：只用于检索与筛选，允许留空（留空表示清掉）。"""

    author: str = Field(default='', max_length=200)


@lru_cache(maxsize=24)
def cached_page(path, stamp, page, image):
    with pdf_engine.open_pdf(Path(path)) as pdf:
        p=pdf[page-1]
        try: return pdf_engine.page_png(p) if image else pdf_engine.page_data(p)
        finally: p.close()


def page_result(doc_id,page,image=False):
    meta=document(doc_id)
    if page<1 or page>meta['pages']: raise HTTPException(400,'页码超出范围')
    path=document_path(meta);stat=path.stat()
    return cached_page(str(path.resolve()),(stat.st_size,stat.st_mtime_ns),page,image)


class Progress(BaseModel):
    page: int = Field(ge=1)


class Search(BaseModel):
    voice_original: str = Field(default='',max_length=8000)
    request_id: str = Field(default='',pattern=r'^(?:[a-f0-9]{32})?$')
    query: str = Field(min_length=1, max_length=8000)
    document_id: str
    page: int = Field(default=1, ge=1)
    cross_book: bool = False
    scope: Literal['all','tags','documents'] = 'all'
    scope_tags: list[str] = Field(default_factory=list,max_length=30)
    scope_documents: list[str] = Field(default_factory=list,max_length=500)
    mode: Literal['close','broad'] = "close"
    selection: str = Field(default="", max_length=12000)
    model_knowledge: bool = False
    web: bool = False
    # 用户显式要求"参考整篇文献"：自动判断只是默认值，必须留一个不会判错的开关。
    whole_document: bool = False
    web_query: str = Field(default='', max_length=1500)
    search_token: str = Field(default='',max_length=100)
    # Phase 8：把界面上那四个平级开关收成两个受控选项。
    #   reading_scope —— 读哪里：auto(交给自动判断) / local(当前文献局部) / document(当前文献全文) / corpus(跨文献)
    #   reading_rigor —— 读多深：auto / fast(快速) / standard(标准) / exhaustive(全面核查)
    # **字段名不能叫 `scope`**：`scope` 在本模型里早已是"跨文献时选哪些文献"（all/tags/documents），
    # 重名会在 Pydantic 里直接覆盖它，跨文献检索当场失效（本轮实测踩到，两个用例立刻变红）。
    # 两者都**只影响既有行为的选择**，不新增任何外发：`document_only` 时一律不查外部，
    # 与既有 `web`/`model_knowledge` 的权限语义一致（许可不是命令）。
    # `auto` 是默认值，此时沿用 `cross_book` / `whole_document` 的旧口径（老客户端不受影响）。
    reading_scope: Literal['auto', 'local', 'document', 'corpus'] = 'auto'
    reading_rigor: Literal['auto', 'fast', 'standard', 'exhaustive'] = 'auto'
    source_policy: Literal['auto', 'document_only'] = 'auto'
    # Phase 7：由报告里的"证据缺口"发起的**定向外部查证**。三项都只是把**已经发给过模型**的
    # 那一小段内容再指认一次（段落编号 + 该段自身给出的论断性质 + 它在原文里的摘引），
    # 不引入任何新的外发内容。只有用户点那个按钮、并走完既有的关键词批准门之后才会真的联网。
    gap_claim: int = Field(default=0, ge=0, le=999)
    gap_type: Literal['', 'DOCUMENT_INTERPRETATION', 'CONCEPT_DEFINITION', 'SCHOLARLY_POSITION',
        'HISTORICAL_FACT', 'BIBLIOGRAPHIC_FACT', 'CONTESTED_INTERPRETATION', 'CURRENT_INFORMATION',
        'BACKGROUND_EXPLANATION'] = ''
    gap_quote: str = Field(default='', max_length=600)


class DocumentTags(BaseModel):
    tags: list[str] = Field(default_factory=list,max_length=30)


class QuoteQuery(BaseModel):
    """引用定位请求：只发送**已经逐字核验过的摘引**，不发送整页或整篇文本。"""
    quote: str = Field(min_length=1, max_length=2000)


def scope_ids(body):
    if not body.cross_book:return {body.document_id}
    docs=documents()
    if body.scope=='all':return {d['id'] for d in docs}
    if body.scope=='tags':
        chosen=set(body.scope_tags)
        result={d['id'] for d in docs if chosen.intersection(d['tags'])}
    else:result={d['id'] for d in docs if d['id'] in body.scope_documents}
    if not result:raise HTTPException(400,'所选跨文献范围为空，请选择标签或文献')
    # Current page and explicit selection remain the reading context.
    return result | {body.document_id}

def retrieve(body,limit=8):
    meta=document(body.document_id)
    if meta.get('storage_kind')=='zotero': document_path(meta)
    if body.page > meta['pages']:
        raise HTTPException(400, "页码超出范围")
    with db() as c:
        rows = c.execute("SELECT chunks.*,documents.name FROM chunks JOIN documents ON documents.id=chunks.document_id" +
                         ("" if body.cross_book else " WHERE document_id=?"), () if body.cross_book else (body.document_id,)).fetchall()
    chunks = [dict(r) for r in rows]
    active_ids = scope_ids(body)
    chunks = [c for c in chunks if c['document_id'] in active_ids]
    from .structure_store import indexed_chunks
    scoped={d['id']:d for d in documents() if d['id'] in active_ids};versions={}
    for node in indexed_chunks(active_ids):
        did=node['document_id']
        if did not in scoped:continue
        if did not in versions:
            stat=document_path(scoped[did]).stat();versions[did]=[stat.st_size,stat.st_mtime_ns]
        if versions[did]==node['index_version']:chunks.append({**node,'name':scoped[did]['name']})
    # 译文作为独立的派生来源参与检索（需求：检索也要能命中译文）。它们不写进 chunks：
    # 那张表是原文索引，引用核验与阅读都依赖它只含原文；这里的行带 translation 标记，
    # 命中后界面会注明"这是译文"，并可跳回它对应的原文位置。
    from .translation import search_chunks as translation_chunks
    known={c['id'] for c in chunks}
    for node in translation_chunks(sys.modules[__name__], active_ids):
        if node['id'] in known:
            continue
        chunks.append({**node,'name':scoped.get(node['document_id'],{}).get('name','文献')})
    retrieval_query = body.query + ('\n' + body.selection if body.selection else '')
    lexical = rank(retrieval_query, chunks, body.document_id, body.page, body.mode,limit=limit)
    semantic=[]
    if not embeddings.enabled() and chunks:
        from .retrieval import local_vectors
        vectors = local_vectors([retrieval_query] + [c['text'] for c in chunks])
        q = vectors[0]
        semantic = sorted(({**c, 'score':sum(value*v.get(t,0) for t,value in q.items())} for c,v in zip(chunks,vectors[1:])), key=lambda c:c['score'], reverse=True)
        semantic = [c for c in semantic[:limit] if c['score']>0]
    if embeddings.enabled():
        try:
            semantic = embeddings.semantic_rank(retrieval_query, chunks, DATA, body.document_id, body.page, body.mode,limit=limit)
        except Exception as exc:
            cancellation.check()
            lexical=[{**r,'retrieval_warning':'外部语义检索暂不可用，已使用本地关键词；局部阅读将扩大原文范围。'} for r in lexical]
    # Reciprocal-rank fusion preserves strong exact-term hits and semantic matches.
    merged = {}
    for ranking in (semantic,lexical):
        for i,row in enumerate(ranking):
            if row['id'] not in merged:
                merged[row['id']] = {**row,'score':0}
            merged[row['id']]['score'] += 1/(60+i+1)
    results=sorted(merged.values(),key=lambda r:r['score'],reverse=True)
    rerank_config=settings.runtime('rerank')
    if rerank_config['enabled']:
        try:
            reranked=model_services.rerank(retrieval_query,results,rerank_config)
            # For reading, reranking changes order only, never discards locator candidates.
            return reranked+[r for r in results if r['id'] not in {x['id'] for x in reranked}] if limit is None else reranked[:limit]
        except Exception as exc:
            cancellation.check()
            results=[{**r,'retrieval_warning':'外部重排暂不可用，已使用本地排序。'} for r in results]
    from .retrieval import local_rerank
    return local_rerank(retrieval_query, results,limit=limit)


class Region(BaseModel):
    page: int = Field(ge=1)
    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)


class Note(BaseModel):
    page: int = Field(ge=0)
    quote: str = Field(default="",max_length=12000)
    content: str = Field(min_length=1,max_length=20000)


class Annotation(BaseModel):
    page: int = Field(ge=1)
    quote: str = Field(default='', max_length=12000)
    content: str = Field(default='', max_length=20000)
    rects: list[tuple[float,float,float,float]] = Field(default_factory=list, max_length=2000)
    color: Literal['yellow','green','blue','pink','purple','orange'] = 'yellow'
    style: Literal['highlight','underline','margin','region','page'] = 'highlight'
    # 从译文栏加批注时，这里存"用户在译文里真正选中的那一小段"（锚点仍是上面的原文）。
    translation_excerpt: str = Field(default='', max_length=12000)


class AnnotationEdit(BaseModel):
    quote: str | None = Field(default=None,max_length=12000)
    content: str = Field(max_length=20000)
    color: Literal['yellow','green','blue','pink','purple','orange'] = 'yellow'
    style: Literal['highlight','underline','margin','region','page'] = 'highlight'
    translation_excerpt: str | None = Field(default=None, max_length=12000)


app.mount("/legal",StaticFiles(directory=ROOT / "legal"),name="legal")
app.mount("/static",StaticFiles(directory=ROOT / "static"),name="static")


@app.get("/")
def index():
    # 静态资源的版本号由服务端注入：以前是手写 ?v=18，改了 JS/CSS 却忘了改版本号时，
    # 浏览器会继续用缓存的旧文件——用户看到的现象是"改了但没生效"。现在按静态文件的
    # 最新修改时间生成，属于同一个部署就必然一致，也不需要在多处同步维护。
    page = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    # HTML 不缓存：否则浏览器可能一直拿着旧的版本号，新资源永远加载不到。
    return Response(page.replace("__ASSET_VERSION__", asset_version()), media_type="text/html",
                    headers={"Cache-Control": "no-cache"})


def asset_version():
    try:
        newest = max((path.stat().st_mtime_ns for path in (ROOT / "static").glob("*") if path.is_file()), default=0)
    except OSError:
        newest = 0
    return f"{newest:x}"


from .plugins import install_builtin_features
import sys
install_builtin_features(features,sys.modules[__name__])
features.mount(app)

@app.get('/api/features')
def installed_features():
    return {'external_plugins':False,'features':features.describe()}
