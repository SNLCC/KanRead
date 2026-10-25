"""Internal reader feature. Explicit host services; no external plugin loading."""

def install(router, host):
    from app import parsing, page_masks, doc_navigation
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

        被「无关区域」屏蔽的页眉/页码同样不参与定位：摘引若只出现在遮罩里，
        这里会如实返回"没找到"，而不是把框画回用户已经声明为噪声的那块版面。
        """
        from app import citation,page_masks
        from app.reading_adapter import extracted_page,native_page,ocr_layout
        meta=host.document(doc_id)
        if not 1<=page<=meta['pages']:raise host.HTTPException(400,'页码超出范围')
        path=host.document_path(meta);st=path.stat();version=(st.st_size,st.st_mtime_ns)
        quote=body.quote
        masks=page_masks.all_regions(doc_id)

        def page_text(number,allow_ocr=False):
            """取该页的阅读文本。默认只用文本层与已有缓存，避免定位时又去识别整本。"""
            saved=parsing.get(doc_id,number,path)
            if saved is not None:return saved[0],saved[1]
            regions=page_masks.split_regions(masks,number)
            if allow_ocr:
                return extracted_page(str(path),version,number,True,regions)
            return native_page(str(path),version,number,regions)['text'],'native_text'

        def locate_on(number,allow_ocr):
            data=host.page_result(doc_id,number)
            text,origin=page_text(number,allow_ocr)
            # 行框与"文字是否被校订过"无关：它就是版面上那一行的位置。
            # 定位按文字匹配，改写过的行自然对不上，因此保留行框不会框错位置；
            # 只有确实没有存过行框、又确认这页做过识别时，才重新识别一次。
            lines=parsing.get_boxes(doc_id,number,path)
            if not lines and origin=='ocr':
                lines=ocr_layout(str(path),version,number)
            # 行框保存的是完整版面（保存时还不知道将来的规则），定位前按当前遮罩过滤。
            lines=page_masks.filter_boxes(lines,page_masks.split_regions(masks,number))
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

    # ---- 无关区域（排版噪声）屏蔽 --------------------------------------------------
    # 规则本身只存本机 reader.sqlite3；任何接口都不改 PDF 字节。
    from pydantic import BaseModel, Field
    from typing import Literal

    class MaskInput(BaseModel):
        # 用户是在**某一页**上画的框：reuse='all' 时把它变成作用于所有页的规则，
        # 但页码本身仍是必填的——它描述"从哪一页画出来的"，也用于返回值排序。
        page: int = Field(ge=1, description='画框所在的页码')
        reuse: Literal['page', 'all'] = 'page'
        x0: float = Field(ge=0, le=1)
        y0: float = Field(ge=0, le=1)
        x1: float = Field(ge=0, le=1)
        y1: float = Field(ge=0, le=1)

    @router.get('/api/documents/{doc_id}/masks')
    def masks(doc_id: str):
        """列出这篇文献的全部无关区域规则 + 规则版本 + 索引重建状态。"""
        host.document(doc_id)
        return {'masks': page_masks.list_masks(doc_id), 'revision': page_masks.revision(doc_id),
                'index_status': page_masks.index_status(doc_id)}

    @router.post('/api/documents/{doc_id}/masks')
    def add_mask(doc_id: str, body: MaskInput):
        """新增规则并重建受影响页的检索索引。

        页数少的文献同步重建，接口返回时已经生效；页数多的返回 "running"，
        前端必须如实显示"正在重建索引"，不能假装检索已经干净。
        """
        meta = host.document(doc_id)
        if not 1 <= body.page <= meta['pages']:
            raise host.HTTPException(400, '页码超出范围')
        page = 0 if body.reuse == 'all' else body.page
        try:
            mask, created = page_masks.add(doc_id, page, body.x0, body.y0, body.x1, body.y1)
        except ValueError as exc:
            raise host.HTTPException(400, str(exc)) from None
        status = None
        if created:
            # 重复添加（同一范围）是幂等的：不重复重建，也不报错。
            status = page_masks.rebuild(host, doc_id, page_masks.affected_pages(page, meta['pages']))
        return {'mask': mask, 'created': created, 'masks': page_masks.list_masks(doc_id),
                'revision': page_masks.revision(doc_id),
                'index_status': status or page_masks.index_status(doc_id)}

    @router.delete('/api/documents/{doc_id}/masks/{mask_id}')
    def remove_mask(doc_id: str, mask_id: str):
        """删除规则并重建受影响页的索引；删除后阅读与检索恢复原文（PDF 从未被改过）。"""
        meta = host.document(doc_id)
        removed = page_masks.remove(doc_id, mask_id)
        if removed is None:
            raise host.HTTPException(404, '这条屏蔽规则不存在或已被删除')
        status = page_masks.rebuild(host, doc_id, page_masks.affected_pages(removed['page'], meta['pages']))
        return {'removed': removed, 'masks': page_masks.list_masks(doc_id),
                'revision': page_masks.revision(doc_id), 'index_status': status}

    @router.get('/api/documents/{doc_id}/masks/status')
    def masks_status(doc_id: str):
        """索引重建进度：running / done / failed，失败时带原因（不静默兜底）。"""
        host.document(doc_id)
        return page_masks.index_status(doc_id)

    # ---- 文档导航层（导航索引试点，默认关闭） ----------------------------------
    # 口径：导航表**只用于**"该读哪几页"的范围建议，绝不进入回答内容、证据或核验；
    # 默认关闭；这一版不调用模型（标签来自章节标题 / 页面首行），因此没有 token 成本；
    # 文件或无关区域规则一变就过期，生成失败/未生成时规划照旧逐页读原文。
    class NavigationBuildInput(BaseModel):
        # enabled 不传＝保持原启用状态（新文献默认关闭）；rebuild 不传＝已是最新就不重算。
        enabled: bool | None = None
        rebuild: bool = False

    class NavigationEnabledInput(BaseModel):
        enabled: bool

    def _navigation_payload(doc_id: str, state=None):
        state = state or doc_navigation.status(doc_id)
        nodes = doc_navigation.preview(doc_id)
        return {**state, 'nodes': nodes,
                'nodes_truncated': int(state.get('node_count') or 0) > len(nodes)}

    @router.get('/api/documents/{doc_id}/navigation')
    def navigation(doc_id: str):
        """状态 + 节点预览。预览不要求已启用：用户可以先看清楚生成了什么再决定开不开。"""
        host.document(doc_id)
        return _navigation_payload(doc_id)

    @router.post('/api/documents/{doc_id}/navigation')
    def build_navigation(doc_id: str, body: NavigationBuildInput):
        """生成/重建导航表。页数多时先返回 building，由 GET 轮询进度（不阻塞请求）。"""
        meta = host.document(doc_id)
        try:
            path = host.document_path(meta)
        except host.HTTPException as exc:
            # 附件不可用：状态如实记为 failed（而不是假装"从没生成过"），让界面能显示原因；
            # 接口本身仍按既有语义回 404/409，用户看到的是可照做的说明。
            doc_navigation.mark_failed(doc_id, str(getattr(exc, 'detail', exc))[:300])
            raise
        stat = path.stat()
        stamp = (stat.st_size, stat.st_mtime_ns)
        state = doc_navigation.build(doc_id, path, stamp, page_masks.revision(doc_id),
                                     int(meta['pages']), rebuild=body.rebuild)
        if body.enabled is not None:
            state = doc_navigation.set_enabled(doc_id, body.enabled)
        return _navigation_payload(doc_id, state)

    @router.put('/api/documents/{doc_id}/navigation/enabled')
    def enable_navigation(doc_id: str, body: NavigationEnabledInput):
        """启用/关闭。默认关闭；开启后只在"ready"时才会被规划真正使用。"""
        host.document(doc_id)
        return _navigation_payload(doc_id, doc_navigation.set_enabled(doc_id, body.enabled))

    @router.delete('/api/documents/{doc_id}/navigation')
    def remove_navigation(doc_id: str):
        """删除导航表与状态：可随时撤销，之后规划完全回到改造前的路径。"""
        host.document(doc_id)
        return _navigation_payload(doc_id, doc_navigation.delete(doc_id))

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
        # 与阅读规划共用同一份（按遮罩版本区分的）缓存：被屏蔽的页眉文字
        # 不应再作为"标题规则"混进目录。
        tree=document_structure(str(path),(st.st_size,st.st_mtime_ns),meta['pages'],
                                page_masks.all_regions(doc_id))
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
