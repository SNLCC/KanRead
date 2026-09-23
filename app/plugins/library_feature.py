"""Internal library feature. Explicit host services; no external plugin loading."""

def install(router, host):
    @router.get('/api/documents')
    def documents(trash: bool=False):
        with host.db() as c:
            return [dict(r) | {'tags': host.json.loads(r['tags'])} for r in c.execute('SELECT * FROM documents WHERE deleted=? ORDER BY created DESC, rowid DESC', (int(trash),))]

    @router.delete('/api/documents/{doc_id}')
    def delete_document(doc_id: str):
        host.document(doc_id)
        with host.db() as c:
            c.execute('UPDATE documents SET deleted=1 WHERE id=?', (doc_id,))
        return {'ok': True}

    @router.post('/api/documents/{doc_id}/restore')
    def restore_document(doc_id: str):
        with host.db() as c:
            if not c.execute('SELECT id FROM documents WHERE id=?', (doc_id,)).fetchone():
                raise host.HTTPException(404, '文档不存在')
            c.execute('UPDATE documents SET deleted=0 WHERE id=?', (doc_id,))
        return {'ok': True}

    @router.post('/api/documents/{doc_id}/purge')
    def purge_document(doc_id: str, body: host.PurgeConfirmation):
        with host.db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=? AND deleted=1', (doc_id,)).fetchone()
            if not row:
                raise host.HTTPException(404, '请先将文献移入应用回收站')
            if body.confirm_name != row['name']:
                raise host.HTTPException(400, '文献名称确认不匹配')
            host.cancellation.cancel_document(doc_id)
            from app import structure_store
            original_path=host.DATA/f'{doc_id}.pdf' if row['storage_kind'] in ('copy','zotero_cache') else row['external_path'] if 'external_path' in row.keys() else None
            if row['storage_kind'] in ('copy', 'zotero_cache'):
                if not __import__('re').fullmatch('[a-f0-9]{32}', doc_id):
                    raise host.HTTPException(400, '文献标识无效')
                path = host.DATA / f'{doc_id}.pdf'
                if path.resolve().parent != host.DATA.resolve():
                    raise host.HTTPException(400, '副本路径异常')
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    raise host.HTTPException(409, '副本正在使用，请稍后重试') from None
            # 彻底删除必须把这篇文献在本机留下的正文与派生内容一起清掉。
            # translation_segments / translation_pages 曾被漏掉（第 104 轮实测：4 篇已彻底删除的
            # 文献留下 36 条译文段落、5 条翻译页记录，永久留在盘上）。它们与 chunks 同在
            # reader.sqlite3（见 app/main.py 的 db()），且这两张表没有外键、app/translation.py 里
            # 也没有任何 DELETE——不在这里删就没有任何地方会删。译文含原文与译文全文，
            # 对标"删除即不留残余"的口径，必须一起清。
            for table in ('chunks', 'notes', 'messages', 'annotations',
                          'translation_segments', 'translation_pages'):
                c.execute(f'DELETE FROM {table} WHERE document_id=?', (doc_id,))
            c.execute('DELETE FROM documents WHERE id=?', (doc_id,))
        from app import parsing
        with parsing.connect() as c:c.execute('DELETE FROM page_edits WHERE document_id=?',(doc_id,))
        cache = host.DATA / 'vectors.sqlite3'
        if cache.exists():
            with host.sqlite3.connect(cache, timeout=30) as conn:
                conn.execute('DELETE FROM vectors')
                conn.commit()
                conn.execute('VACUUM')
        with host.sqlite3.connect(host.DATA / 'reader.sqlite3', timeout=30) as conn:
            conn.execute('VACUUM')
        host.cached_page.cache_clear()
        from app.reading_adapter import extracted_page
        extracted_page.cache_clear()
        from app.reading_store import purge
        purge(host.DATA,doc_id)
        structure_store.purge(doc_id,original_path)
        from app.reading_adapter import native_page,document_structure
        native_page.cache_clear();document_structure.cache_clear()
        return {'ok': True, 'original_untouched': True}

    @router.patch('/api/documents/{doc_id}')
    def rename_document(doc_id: str, body: host.DocumentName):
        host.document(doc_id)
        name = body.name.strip()
        if not name:
            raise host.HTTPException(400, '请输入文献名称')
        with host.db() as c:
            c.execute('UPDATE documents SET name=? WHERE id=?', (name, doc_id))
        return host.document(doc_id)

    @router.put('/api/documents/{doc_id}/author')
    def set_document_author(doc_id: str, body: host.DocumentAuthor):
        """作者：只用于检索与筛选，来源于 PDF 元数据或用户自己填。"""
        host.document(doc_id)
        author = body.author.strip()[:200]
        with host.db() as c:
            c.execute('UPDATE documents SET author=? WHERE id=?', (author, doc_id))
        return host.document(doc_id)

    @router.post('/api/documents')
    @host.settings.frozen
    def upload(file: host.UploadFile):
        doc_id = host.uuid.uuid4().hex
        path = host.DATA / f'{doc_id}.pdf'
        try:
            size = 0
            with path.open('wb') as output:
                while (block := file.file.read(1024 * 1024)):
                    size += len(block)
                    if size > 100 * 1024 * 1024:
                        raise host.HTTPException(413, 'PDF 不能超过 100 MB')
                    output.write(block)
            try:
                count = host.pdf_engine.validate(path)
            except Exception as exc:
                raise host.HTTPException(400, '请导入未加密的有效 PDF，且不超过 3000 页') from exc
            chunks = []
            author = ''
            with host.pdf_engine.open_pdf(path) as pdf:
                # 作者优先取 PDF 元数据；没有就留空，由用户在文献工作台自己填。
                try:
                    author = host.pdf_engine.document_author(path)
                except Exception:
                    author = ''
                for index in range(len(pdf)):
                    page = pdf[index]
                    try:
                        for span in host.pdf_engine.page_data(page)['spans']:
                            for start in range(0, len(span['text']), 1000):
                                chunks.append((host.uuid.uuid4().hex, doc_id, index + 1, span['text'][start:start + 1200], host.json.dumps(span['bbox']), 'visual'))
                    finally:
                        page.close()
            with host.db() as c:
                c.execute('INSERT INTO documents(id,name,pages,author) VALUES(?,?,?,?)',
                          (doc_id, (file.filename or '未命名.pdf').replace('\\', '/').split('/')[-1], count, author))
                c.executemany('INSERT INTO chunks(id,document_id,page,text,bbox,bbox_space) VALUES(?,?,?,?,?,?)', chunks)
        except Exception as exc:
            path.unlink(missing_ok=True)
            if isinstance(exc, host.HTTPException):
                raise
            raise host.HTTPException(400, 'PDF 解析失败，请检查文件是否损坏') from exc
        warning = None
        if host.embeddings.enabled():
            try:
                host.embeddings.vectors_for([{'text': c[3]} for c in chunks], host.DATA)
            except Exception:
                warning = 'PDF 已保存；语义索引建立失败，请检查 Embedding 服务。检索时将重试。'
        return {**host.document(doc_id), 'chunks': len(chunks), 'warning': warning}

    @router.put('/api/documents/{doc_id}/tags')
    def set_document_tags(doc_id: str, body: host.DocumentTags):
        host.document(doc_id)
        tags = list(dict.fromkeys((t.strip() for t in body.tags if t.strip())))
        if any((len(t) > 50 or any((ord(ch) < 32 for ch in t)) for t in tags)):
            raise host.HTTPException(400, '标签最多 50 个字符，不能含控制字符')
        with host.db() as c:
            c.execute('UPDATE documents SET tags=? WHERE id=?', (host.json.dumps(tags, ensure_ascii=False), doc_id))
        return host.document(doc_id)
    return {'documents': documents,'delete_document': delete_document,'restore_document': restore_document,'purge_document': purge_document,'rename_document': rename_document,'upload': upload,'set_document_tags': set_document_tags}
