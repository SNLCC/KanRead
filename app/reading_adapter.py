"""SQLite/PDF adapter for the independent literature reading core."""
import hashlib
import re
from functools import lru_cache
from . import pdf_engine,cancellation,structure_store
from .literature_core.structure import text_quality,section_tree,expand_sections,heading,layout_text,visual_nodes


@lru_cache(maxsize=256)
def native_page(path,version,page):
    saved=structure_store.page_get(path,('layout-v1',*version),page)
    if saved is not None:return saved
    with pdf_engine.open_pdf(path) as pdf:
        item=pdf[page-1]
        try:
            data=pdf_engine.page_data(item)
            text,two=layout_text(data['spans'],data['width'])
            # Retain text when a PDF has no usable geometry.
            text=text or data['text']
            images=sum(1 for _ in item.get_objects(filter=[pdf_engine.raw.FPDF_PAGEOBJ_IMAGE]))
            result={'text':text,'two_columns':two,'nodes':visual_nodes(text,page),'quality':text_quality(text),'images':images}
            structure_store.page_put(path,('layout-v1',*version),page,result)
            return result
        finally:item.close()


@lru_cache(maxsize=32)
def document_structure(path,version,total):
    from pypdf import PdfReader
    entries=[]
    try:
        reader=PdfReader(path)
        def walk(items,level=1):
            for item in items:
                if isinstance(item,list):walk(item,level+1)
                else:
                    number=reader.get_destination_page_number(item)
                    if number is not None:entries.append({'title':str(item.title),'page':number+1,'level':level,'origin':'PDF 目录'})
        walk(reader.outline)
    except Exception:pass
    if not entries:
        for page in range(1,total+1):
            cancellation.check()
            data=native_page(path,version,page)
            for line in data['text'].splitlines():
                item=heading(line)
                if item:entries.append({**item,'page':page,'origin':'标题规则（可能不完整）'})
    return section_tree(entries,total)


def planning_context(host,body,history):
    doc=host.document(body.document_id);path=host.document_path(doc);stat=path.stat()
    tree=document_structure(str(path),(stat.st_size,stat.st_mtime_ns),doc['pages'])
    return {'history':history,'document':doc['name'],'page':body.page,'pages':doc['pages'],
            'outline':[{'title':n['title'],'page':n['page'],'end':n['end_page']} for n in tree[:80]],
            'outline_complete':len(tree)<=80,'cross_documents':body.cross_book}


@lru_cache(maxsize=128)
def extracted_page(path,version,page,ocr):
    data=native_page(path,version,page)
    text=data['text'];kind='native_text'
    if not data['quality']['suspect'] or not ocr:return text.replace('\r\n','\n').replace('\r','\n'),kind
    with pdf_engine.open_pdf(path) as pdf:
        item=pdf[page-1]
        try:
            image=pdf_engine.render(item,min(2,2400/max(item.get_size())))
        finally:item.close()
    # OCR is CPU-heavy and does not need the PDFium lock: keep page turns responsive.
    try:recognized,lines=pdf_engine.recognize_layout(image)
    finally:image.close()
    if recognized and (not text or text_quality(recognized)['bad_ratio']<data['quality']['bad_ratio']):
        text=recognized;kind='ocr'
        # 保留识别到的行框（换算成页面比例）：扫描页没有文本层，
        # 引用要能落到具体区域就只能靠它。内存缓存 + 调用方决定是否落库。
        page_width,page_height=image.width,image.height
        _ocr_layout[(str(path),version,page)]=[
            {'text':line['text'],'bbox':[line['bbox'][0]/page_width,line['bbox'][1]/page_height,
                                        line['bbox'][2]/page_width,line['bbox'][3]/page_height]}
            for line in lines if line.get('bbox') and page_width and page_height]
    return text.replace('\r\n','\n').replace('\r','\n'),kind


# 扫描页 OCR 行框的进程内缓存（键与 extracted_page 一致）。落库由读文路径负责；
# 没有落库时（例如升级前读过的页、或重启之后）由 ocr_layout() 按需重算一次。
_ocr_layout={}


def ocr_layout(path,version,page):
    """取该页的 OCR 行框；缓存里没有就重新识别一次（只在这页确实需要定位引用时）。"""
    key=(str(path),version,page)
    if key in _ocr_layout:return _ocr_layout[key]
    with pdf_engine.open_pdf(path) as pdf:
        item=pdf[page-1]
        try:
            image=pdf_engine.render(item,min(2,2400/max(item.get_size())))
            try:
                _,lines=pdf_engine.recognize_layout(image)
                width,height=image.width,image.height
            finally:image.close()
        finally:item.close()
    _ocr_layout[key]=[{'text':line['text'],'bbox':[line['bbox'][0]/width,line['bbox'][1]/height,
                                                 line['bbox'][2]/width,line['bbox'][3]/height]}
                      for line in lines if line.get('bbox') and width and height]
    return _ocr_layout[key]


def forget_ocr_layout(path=None):
    if path is None:_ocr_layout.clear();return
    for key in [k for k in _ocr_layout if k[0]==str(path)]:_ocr_layout.pop(key,None)


def document_text_state(host,document_id):
    """这篇文献在本机**已经索引到的全部文字**（用于跨文献排序，不读文件、不调模型）。

    两个来源缺一不可：`chunks` 是索引到的正文片段；`page_edits` 是整页 OCR 与用户校订
    （扫描件的文字只在这里）。取不到时返回空串——排序信号少一份，只是顺序不同，不影响读不读。
    """
    parts=[]
    try:
        with host.db() as c:
            parts=[row[0] for row in c.execute(
                'SELECT text FROM chunks WHERE document_id=?',(document_id,)).fetchall() if row[0]]
    except Exception:
        parts=[]
    try:
        from . import parsing as parsing_module
        with parsing_module.connect() as c:
            parts+=[row[0] for row in c.execute(
                'SELECT text FROM page_edits WHERE document_id=? AND LENGTH(TRIM(text))>0',
                (document_id,)).fetchall() if row[0]]
    except Exception:
        pass
    return '\n'.join(parts)


def read_documents(host,body,plan):
    ids=host.scope_ids(body)
    docs=[d for d in host.documents() if d['id'] in ids]
    docs.sort(key=lambda d:(d['id']!=body.document_id,d['name'],d['id']))
    candidates=len(docs);located={};selected=docs;discovery=None
    if plan.breadth=='discovery' and body.cross_book:
        # Discovery is a relevance decision made from each document, not a lexical exclusion gate.
        # All authorized documents participate, including zero-hit/cross-language documents.
        #
        # 第 84 轮补上的**只影响顺序**的那一半（§37 的两级召回、§43 Phase 9）：按"当前文献优先 →
        # 本机命中数降序 → 名称 → id"排序，预算用尽时先读到最相关的几篇。
        # 集合**一篇都不减**——第 70 轮"用命中与否决定读多少"正是被
        # `test_discovery_includes_zero_lexical_hit_documents` 挡住的，那条规则没有变。
        from .literature_core import discovery
        terms=discovery.terms_of(body.query)
        scores=discovery.document_scores(terms,{d['id']:document_text_state(host,d['id']) for d in docs})
        docs=discovery.order_documents(docs,scores,body.document_id)
        selected=docs
        discovery=discovery.discovery_report(docs,scores,body.document_id)
        discovery['terms']=terms
    elif plan.breadth=='local':
        for doc in docs:
            if doc['id']==body.document_id and plan.pages:
                if max(plan.pages)>doc['pages']:raise host.HTTPException(400,'问题指定的页码超出当前文献')
                located[doc['id']]=set(plan.pages)
            elif doc['id']==body.document_id and (body.selection or '当前页' in body.query or '这一页' in body.query or '本页' in body.query or 'current page' in body.query.lower() or 'this page' in body.query.lower()):
                located[doc['id']]={body.page}
            else:
                hits=[]
                for query in dict.fromkeys((body.query,*plan.queries)):
                    cancellation.check()
                    hits.extend(host.retrieve(body.model_copy(update={'document_id':doc['id'],'query':query,'page':min(body.page,doc['pages']),'cross_book':False}),limit=None))
                located[doc['id']]=set() if any(r.get('retrieval_warning') for r in hits) else {r['page'] for r in hits}
                # No reliable location (including cross-language lexical miss): read it all.
                if not located[doc['id']]:located.pop(doc['id'])
    sources=[];reports=[]
    for doc in selected:
        cancellation.check()
        path=host.document_path(doc);stat=path.stat();version=(stat.st_size,stat.st_mtime_ns)
        pages=set(range(1,doc['pages']+1))
        tree=document_structure(str(path),version,doc['pages'])
        if doc['id'] in located:
            pages=expand_sections(located[doc['id']],tree,doc['pages'])
        read=[];missing=[];ocr_pages=[];quality_pages=[];unparsed=[];nodes=[];columns=[]
        # 进度文案要**说清这一步到底在做什么**（第 90 轮用户反馈："上一个问题 OCR 过、读过全文，
        # 下一个问题为什么还要重新 OCR 和读取"）。事实是：页面文字与视觉解读都按页缓存在本机，
        # 命中就直接用；真正每轮都会重来的是**模型对原文的阅读**（它是对着这一轮的问题读的）。
        # 因此这里把"已保存直接复用"与"真的在做本机 OCR"分开报，不再一律写 OCR。
        for page in sorted(pages):
            cancellation.check()
            from . import parsing
            saved=parsing.get(doc['id'],page,path)
            cancellation.progress('读取本机已保存的原文' if saved is not None else '解析原文 / 空文本页本机识别',
                len(read)+len(missing)+1,len(pages))
            text,kind=saved if saved is not None else extracted_page(str(path),version,page,True)
            if saved is None and kind=='ocr' and text:
                parsing.cache(doc['id'],page,path,text,kind)
                # 行框一并保存：重启后仍然能把引用定位到扫描页的具体区域。
                parsing.cache_boxes(doc['id'],page,path,_ocr_layout.get((str(path),version,page)) or [])
            info=native_page(str(path),version,page)
            if info['two_columns']:columns.append(page)
            nodes.extend(info['nodes'])
            cancellation.check()
            # 取不到文字的页单独归类后立即结束本页处理：它既不算已读，也不该按“文本可疑”
            # 计入质量页。历史缺陷是这里先记质量页再 continue，而 full_extracted_text 要求
            # quality_pages 为空，于是任何扫描件都会永久把“已取得全文”压成假，即便 OCR 成功。
            if not text:
                if info['text'].strip():unparsed.append(page)
                else:missing.append(page)
                continue
            if text_quality(text)['suspect']:quality_pages.append(page)
            if kind=='ocr':ocr_pages.append(page)
            read.append(page)
            sources.append({'id':f'{doc["id"]}:p{page}:'+hashlib.sha256(text.encode()).hexdigest()[:16],
                'document_id':doc['id'],'name':doc['name'],'page':page,'text':text,
                'bbox':None,'bbox_space':'visual','source_type':kind,'source_namespace':'LOCAL_DOCUMENT',
                'parse_quality':'疑似解析异常' if page in quality_pages else '双栏重排' if info['two_columns'] else '文本质量检查通过（非语义保证）',
                'visual_nodes':info['nodes'],'has_images':bool(info['images']),'document_version':f'{stat.st_size}:{stat.st_mtime_ns}'})
        reports.append({'document_id':doc['id'],'name':doc['name'],'total_pages':doc['pages'],
            'requested_pages':sorted(pages),'read_pages':read,'missing_pages':missing,'unparsed_pages':unparsed,
            'ocr_pages':ocr_pages,
            'whole_document':len(pages)==doc['pages'],'sections':tree,'visual_nodes':nodes,'quality_pages':quality_pages,'two_column_pages':columns})
        structure_store.structure_put(doc['id'],str(path),version,tree,nodes)
    report={'documents':reports,'candidate_documents':candidates,'selected_documents':len(selected),
        'full_extracted_text':bool(reports) and all(d['whole_document'] and not d['missing_pages'] and not d['unparsed_pages'] and not d['quality_pages'] for d in reports),
        'indexed_pages':sum(len(d['read_pages']) for d in reports),'total_pages':sum(d['total_pages'] for d in reports),
        'breadth':plan.breadth,'reason':plan.reason,'planner':plan.origin}
    if discovery is not None:report['discovery']=discovery
    return sources,report
