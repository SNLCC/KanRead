"""Internal zotero feature. Explicit host services; no external plugin loading."""

def install(router, host):
    @router.get('/api/zotero/settings')
    def zotero_settings():
        return host.zotero.public()

    @router.put('/api/zotero/settings')
    def save_zotero_settings(body: host.zotero.ZoteroSettings):
        return host.zotero.save(body)

    @router.get('/api/zotero/items')
    def zotero_items(q: str='', start: int=0, collection: str=''):
        if start < 0 or len(q) > 500:
            raise host.HTTPException(400, '查询参数无效')
        return host.zotero.listing(q, start, collection)

    @router.get('/api/zotero/collections')
    def zotero_collections():
        return host.zotero.collections()

    @router.get('/api/zotero/diagnose')
    def zotero_diagnose():
        # 只读诊断：确认密钥属于哪个账户、有没有资料库读取权限，并真读一次分类。
        return host.zotero.diagnose()

    @router.post('/api/zotero/items/{item_key}/open')
    def open_zotero(item_key: str):
        attachment = host.zotero.attachment(item_key)
        with host.db() as c:
            old = c.execute('SELECT * FROM documents WHERE external_key=?', (attachment['identity'],)).fetchone()
        if old and old['deleted']:
            raise host.HTTPException(409, '该附件已在应用回收站，请先恢复或彻底移除其应用记录')
        if old and old['name'].strip().lower() in ('full text pdf', 'full text', 'pdf', '全文'):
            with host.db() as c:
                c.execute('UPDATE documents SET name=? WHERE id=?', (attachment['title'], old['id']))
        doc_id = old['id'] if old else host.uuid.uuid4().hex
        external = 'path' in attachment
        path = attachment['path'] if external else host.DATA / f'{doc_id}.pdf'
        staging = None
        if external and old and (old['file_stamp'] == f'{path.stat().st_size}:{path.stat().st_mtime_ns}'):
            return host.document(doc_id)
        try:
            if not external:
                staging = host.DATA / f'{host.uuid.uuid4().hex}.download'
                staging.write_bytes(attachment['bytes'])
                read_path = staging
            else:
                read_path = path
            pages = host.pdf_engine.validate(read_path)
            chunks = []
            with host.pdf_engine.open_pdf(read_path) as pdf:
                for i in range(len(pdf)):
                    page = pdf[i]
                    try:
                        for span in host.pdf_engine.page_data(page)['spans']:
                            for start in range(0, len(span['text']), 1000):
                                chunks.append((host.uuid.uuid4().hex, doc_id, i + 1, span['text'][start:start + 1200], host.json.dumps(span['bbox']), 'visual'))
                    finally:
                        page.close()
            if staging:
                staging.replace(path)
            stamp = f'{path.stat().st_size}:{path.stat().st_mtime_ns}'
            # 作者来自父条目的 creators（见 zotero.creators_author）。两条分支都必须写上：
            # INSERT 少了 author 列的话，新导入的文献作者会一直是空——这正是本轮要修的缺陷。
            author = attachment.get('author','')
            with host.db() as c:
                if old:
                    # 随附件一起刷新：用户在 Zotero 里改了作者后重新打开附件，这里能跟上。
                    c.execute('UPDATE documents SET pages=?,current_page=MIN(current_page,?),external_path=?,file_stamp=?,author=? WHERE id=?', (pages, pages, str(path) if external else '', stamp, author, doc_id))
                    c.execute('DELETE FROM chunks WHERE document_id=?', (doc_id,))
                else:
                    c.execute('INSERT INTO documents(id,name,pages,storage_kind,external_path,external_key,file_stamp,author) VALUES(?,?,?,?,?,?,?,?)', (doc_id, attachment['title'], pages, 'zotero' if external else 'zotero_cache', str(path) if external else '', attachment['identity'], stamp, author))
                c.executemany('INSERT INTO chunks(id,document_id,page,text,bbox,bbox_space) VALUES(?,?,?,?,?,?)', chunks)
            return host.document(doc_id)
        except host.HTTPException:
            raise
        except Exception:
            raise host.HTTPException(400, '附件读取或索引失败，请确认 PDF 有效且未加密') from None
        finally:
            if staging:
                staging.unlink(missing_ok=True)
    return {'zotero_settings': zotero_settings,'save_zotero_settings': save_zotero_settings,'zotero_items': zotero_items,'zotero_collections': zotero_collections,'open_zotero': open_zotero}
