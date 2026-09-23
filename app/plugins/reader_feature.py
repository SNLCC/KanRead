"""Internal reader feature. Explicit host services; no external plugin loading."""

def install(router, host):
    from app import parsing
    @router.get('/api/parsing-settings')
    def parsing_settings():return parsing.config()
    @router.put('/api/parsing-settings')
    def save_parsing_settings(body:parsing.ConfigInput):return parsing.save(body)
    @router.post('/api/parsing-settings/check')
    def check_parsing_service():
        # 只读探测：确认地址指向的是哪一代 MinerU，不发送任何页面内容。
        return parsing.check()
    @router.get('/api/documents/{doc_id}/pages/{page}/text')
    def editable_text(doc_id:str,page:int):
        meta=host.document(doc_id)
        if not 1<=page<=meta['pages']:raise host.HTTPException(400,'页码超出范围')
        path=host.document_path(meta);saved=parsing.get(doc_id,page,path)
        # 用 is not None 而非真假判断：空校订已在校订入口被拒，此处只表达“有无校订记录”，
        # 不再依赖文本内容的真假。
        if saved is not None:return {'text':saved[0],'origin':saved[1]}
        from app.reading_adapter import extracted_page
        st=path.stat();text,origin=extracted_page(str(path),(st.st_size,st.st_mtime_ns),page,True)
        return {'text':text,'origin':origin}
    @router.put('/api/documents/{doc_id}/pages/{page}/text')
    def correct_text(doc_id:str,page:int,body:parsing.PageText):
        if not 1<=page<=host.document(doc_id)['pages']:raise host.HTTPException(400,'页码超出范围')
        parsing.put(host,doc_id,page,body.text,'user_corrected');return {'ok':True}
    @router.post('/api/documents/{doc_id}/pages/{page}/recognize')
    def recognize_page(doc_id:str,page:int):
        if not 1<=page<=host.document(doc_id)['pages']:raise host.HTTPException(400,'页码超出范围')
        return {'text':parsing.recognize(host.document_path(host.document(doc_id)),page),'origin':'recognition_draft'}

    @router.post('/api/documents/{doc_id}/pages/{page}/locate')
    def locate_quote(doc_id:str,page:int,body:host.QuoteQuery):
        """把核验过的摘引定位到这一页的具体区域；这一页找不到就去全文找。

        文本层页面按字形行匹配（双栏重排也成立）；扫描页用识别时保存的 OCR 行框；
        两者都对不上时，按"离请求页最近优先"在**不触发新识别**的页里继续找——
        记录里的页码偶有偏差时，界面因此能跳到真正包含这段文字的那一页，
        而不是把用户丢在一个没有该文字的页面上。整篇都找不到才如实说明。
        """
        from app import citation
        from app.reading_adapter import extracted_page,native_page,ocr_layout
        meta=host.document(doc_id)
        if not 1<=page<=meta['pages']:raise host.HTTPException(400,'页码超出范围')
        path=host.document_path(meta);st=path.stat();version=(st.st_size,st.st_mtime_ns)
        quote=body.quote

        def page_text(number,allow_ocr=False):
            """取该页的阅读文本。默认只用文本层与已有缓存，避免定位时又去识别整本。"""
            saved=parsing.get(doc_id,number,path)
            if saved is not None:return saved[0],saved[1]
            if allow_ocr:
                return extracted_page(str(path),version,number,True)
            return native_page(str(path),version,number)['text'],'native_text'

        def locate_on(number,allow_ocr):
            data=host.page_result(doc_id,number)
            text,origin=page_text(number,allow_ocr)
            # 行框与"文字是否被校订过"无关：它就是版面上那一行的位置。
            # 定位按文字匹配，改写过的行自然对不上，因此保留行框不会框错位置；
            # 只有确实没有存过行框、又确认这页做过识别时，才重新识别一次。
            lines=parsing.get_boxes(doc_id,number,path)
            if not lines and origin=='ocr':
                lines=ocr_layout(str(path),version,number)
            boxes,method=citation.locate_page_text(text,quote,data.get('width'),data.get('height'),data.get('spans'),lines)
            return boxes,method,origin

        boxes,method,origin=locate_on(page,True)
        if boxes:
            return {'page':page,'boxes':boxes,'method':method,'origin':origin,'corrected':False,
                    'message':{'text_layer':'已定位到该页文本层的对应文字。','ocr':'已定位到该页识别出的对应文字区域。'}[method]}
        for candidate in citation.search_order(page,meta['pages']):
            boxes,method,found_origin=locate_on(candidate,False)
            if boxes:
                return {'page':candidate,'boxes':boxes,'method':method,'origin':found_origin,'corrected':True,
                        'message':f'这段文字实际在第 {candidate} 页（记录为第 {page} 页），已按实际位置定位。'}
        return {'page':page,'boxes':[],'method':'none','origin':origin,'corrected':False,
                'message':'这一页和全文都没有找到这段文字：它可能来自模型的转述、图表解读，或该页文字已被你校订改写。'}
    @router.get('/api/documents/{doc_id}/pages/{page}')
    def page_data(doc_id: str, page: int):
        return host.page_result(doc_id, page)

    @router.get('/api/documents/{doc_id}/pages/{page}/image')
    def page_image(doc_id: str, page: int, width: int = 0):
        """页面图像。``width`` 只用于缩小渲染（译文对照里的窄栏），不会放大、不改缓存。"""
        data = host.page_result(doc_id, page, True)
        if width and width > 160:
            try:
                import io
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    ratio = min(1.0, width / max(1, image.width))
                    if ratio < .995:
                        resized = image.convert('RGB').resize((max(1, int(image.width * ratio)), max(1, int(image.height * ratio))), Image.LANCZOS)
                        try:
                            buffer = io.BytesIO(); resized.save(buffer, format='PNG')
                            return host.Response(buffer.getvalue(), media_type='image/png')
                        finally:
                            resized.close()
            except Exception:
                # 缩放失败就返回原图：对照视图宁可大一点，也不能显示不出页面。
                pass
        return host.Response(data, media_type='image/png')

    @router.get('/api/documents/{doc_id}/pages/{page}/thumbnail')
    def thumbnail(doc_id:str,page:int):
        with host.page_document(doc_id,page) as pdf:
            item=pdf[page-1]
            try:
                image=host.pdf_engine.render(item,180/max(item.get_size()))
                try:
                    import io
                    buffer=io.BytesIO();image.save(buffer,format='PNG');return host.Response(buffer.getvalue(),media_type='image/png')
                finally:image.close()
            finally:item.close()

    @router.put('/api/documents/{doc_id}/progress')
    def progress(doc_id: str, body: host.Progress):
        meta = host.document(doc_id)
        if body.page > meta['pages']:
            raise host.HTTPException(400, '页码超出范围')
        with host.db() as c:
            c.execute('UPDATE documents SET current_page=? WHERE id=?', (body.page, doc_id))
        return {'ok': True}

    @router.get('/api/documents/{doc_id}/outline')
    def outline(doc_id:str):
        """目录：优先用 PDF 自带书签，没有就用标题规则扫描，并如实标明来源。

        与阅读规划共用同一份缓存（document_structure 有 lru_cache + SQLite 持久化），
        因此点开目录不会重复扫描整本；没有可靠标题的文献会返回空列表而不是编造条目。
        """
        from app.reading_adapter import document_structure
        meta=host.document(doc_id)
        path=host.document_path(meta);st=path.stat()
        tree=document_structure(str(path),(st.st_size,st.st_mtime_ns),meta['pages'])
        origins=sorted({node.get('origin','') for node in tree if node.get('origin')})
        return {'sections':[{'title':node['title'],'page':node['page'],'level':node.get('level',1),
                             'end_page':node.get('end_page',node['page']),'origin':node.get('origin','')} for node in tree[:500]],
                'total_pages':meta['pages'],'origins':origins,
                'message':'目录来自 PDF 自带书签。' if origins==['PDF 目录'] else
                          'PDF 没有自带书签，目录由标题规则识别，可能不完整。' if tree else
                          '这份文献没有可用的目录信息：可先用缩略图或页码跳转。'}

    @router.post('/api/documents/{doc_id}/ocr')
    def ocr(doc_id: str, body: host.Region):
        if body.x1 <= body.x0 or body.y1 <= body.y0:
            raise host.HTTPException(400, '请选择有效区域')
        meta = host.document(doc_id)
        if body.page > meta['pages']:
            raise host.HTTPException(400, '页码超出范围')
        try:
            if parsing.config()['mode']=='local':
                text, box = host.pdf_engine.region_content(host.document_path(meta), body.page, (body.x0, body.y0, body.x1, body.y1))
            else:
                text=parsing.recognize(host.document_path(meta),body.page,(body.x0,body.y0,body.x1,body.y1));box=None
        except host.HTTPException:raise
        except Exception as exc:
            raise host.HTTPException(503, '本地 OCR 识别失败，请检查安装依赖并尝试扩大选区。') from exc
        if not text:
            raise host.HTTPException(422, '此区域未识别到文字，请扩大选区重试')
        from app.literature_core.structure import reflow_lines
        text_reflowed=reflow_lines(text)
        with host.db() as c:
            exists = c.execute('SELECT id FROM chunks WHERE document_id=? AND page=? AND text=?', (doc_id, body.page, text)).fetchone()
            if not exists:
                # 落库仍用**逐行原文**（检索与引用核验都以原文为准），
                # 交给用户看和编辑的是整理成段的版本。
                c.execute('INSERT INTO chunks(id,document_id,page,text,bbox,bbox_space) VALUES(?,?,?,?,?,?)', (host.uuid.uuid4().hex, doc_id, body.page, text, host.json.dumps(box), 'visual'))
        return {'text': text, 'text_reflowed': text_reflowed}
    return {'page_data': page_data,'page_image': page_image,'progress': progress,'ocr': ocr}
