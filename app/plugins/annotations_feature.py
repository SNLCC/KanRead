"""Internal annotations feature. Explicit host services; no external plugin loading."""

from app import annotated_export


def install(router, host):
    @router.get('/api/documents/{doc_id}/annotations')
    def annotations(doc_id: str):
        host.document(doc_id)
        with host.db() as c:
            return [{**dict(r), 'rects': host.json.loads(r['rects'])} for r in c.execute('SELECT * FROM annotations WHERE document_id=? AND deleted=0 ORDER BY page,rowid', (doc_id,))]

    @router.post('/api/documents/{doc_id}/annotations')
    def create_annotation(doc_id: str, body: host.Annotation):
        if body.page > host.document(doc_id)['pages']:
            raise host.HTTPException(400, '页码超出范围')
        if not body.content.strip() and not body.quote.strip() and not body.rects:
            raise host.HTTPException(400, '请选择原文或填写批注')
        if any((not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1) for x0, y0, x1, y1 in body.rects)):
            raise host.HTTPException(400, '批注位置无效')
        aid = host.uuid.uuid4().hex
        with host.db() as c:
            c.execute('INSERT INTO annotations(id,document_id,page,quote,content,rects,color,style,translation_excerpt)'
                      ' VALUES(?,?,?,?,?,?,?,?,?)',
                      (aid, doc_id, body.page, body.quote, body.content, host.json.dumps(body.rects),
                       body.color, body.style if body.rects else 'page', body.translation_excerpt))
        return {'id': aid}

    @router.patch('/api/documents/{doc_id}/annotations/{aid}')
    def edit_annotation(doc_id: str, aid: str, body: host.AnnotationEdit):
        host.document(doc_id)
        with host.db() as c:
            cursor = c.execute("UPDATE annotations SET quote=COALESCE(?,quote),content=?,color=?,"
                               "translation_excerpt=COALESCE(?,translation_excerpt),"
                               "style=CASE WHEN rects='[]' THEN 'page' ELSE ? END WHERE id=? AND document_id=? AND deleted=0",
                               (body.quote, body.content, body.color, body.translation_excerpt,
                                body.style, aid, doc_id))
            if not cursor.rowcount:
                raise host.HTTPException(404, '批注不存在')
        return {'ok': True}

    @router.delete('/api/documents/{doc_id}/annotations/{aid}')
    def delete_annotation(doc_id: str, aid: str):
        host.document(doc_id)
        with host.db() as c:
            cursor = c.execute('UPDATE annotations SET deleted=1 WHERE id=? AND document_id=?', (aid, doc_id))
            if not cursor.rowcount:
                raise host.HTTPException(404, '批注不存在')
        return {'ok': True}

    @router.post('/api/documents/{doc_id}/annotations/{aid}/restore')
    def restore_annotation(doc_id: str, aid: str):
        host.document(doc_id)
        with host.db() as c:
            cursor = c.execute('UPDATE annotations SET deleted=0 WHERE id=? AND document_id=?', (aid, doc_id))
            if not cursor.rowcount:
                raise host.HTTPException(404, '批注不存在')
        return {'ok': True}

    @router.get('/api/documents/{doc_id}/notes')
    def notes(doc_id: str):
        host.document(doc_id)
        with host.db() as c:
            return [dict(r) for r in c.execute('SELECT * FROM notes WHERE document_id=? AND deleted=0 ORDER BY created DESC, rowid DESC', (doc_id,))]

    @router.post('/api/documents/{doc_id}/notes')
    def save_note(doc_id: str, body: host.Note):
        if body.page > host.document(doc_id)['pages']:
            raise host.HTTPException(400, '页码超出范围')
        with host.db() as c:
            c.execute('INSERT INTO notes(id,document_id,page,quote,content) VALUES(?,?,?,?,?)', (host.uuid.uuid4().hex, doc_id, body.page, body.quote, body.content))
        return {'ok': True}

    @router.patch('/api/documents/{doc_id}/notes/{nid}')
    def edit_note(doc_id:str,nid:str,body:host.Note):
        if body.page>host.document(doc_id)['pages']:raise host.HTTPException(400,'页码超出范围')
        with host.db() as c:
            cursor=c.execute('UPDATE notes SET page=?,quote=?,content=? WHERE id=? AND document_id=? AND deleted=0',(body.page,body.quote,body.content,nid,doc_id))
            if not cursor.rowcount:raise host.HTTPException(404,'笔记不存在')
        return {'ok':True}

    @router.delete('/api/documents/{doc_id}/notes/{nid}')
    def delete_note(doc_id:str,nid:str):
        host.document(doc_id)
        with host.db() as c:
            cursor=c.execute('UPDATE notes SET deleted=1 WHERE id=? AND document_id=?',(nid,doc_id))
            if not cursor.rowcount:raise host.HTTPException(404,'笔记不存在')
        return {'ok':True}

    @router.post('/api/documents/{doc_id}/notes/{nid}/restore')
    def restore_note(doc_id:str,nid:str):
        host.document(doc_id)
        with host.db() as c:
            cursor=c.execute('UPDATE notes SET deleted=0 WHERE id=? AND document_id=?',(nid,doc_id))
            if not cursor.rowcount:raise host.HTTPException(404,'笔记不存在')
        return {'ok':True}

    @router.get('/api/documents/{doc_id}/export')
    def export(doc_id: str):
        name = host.document(doc_id)['name']
        text = f'# {name} · 阅读笔记\n\n' + '\n\n'.join((('## 整篇文献\n\n> ' if n['page']==0 else f'## 第 {n["page"]} 页\n\n> ') + n['quote'].replace('\n', '\n> ') + f'\n\n{n['content']}' for n in host.notes(doc_id)))
        text += '\n\n# 文献批注\n\n' + '\n\n'.join((f'## 第 {a['page']} 页\n\n> {a['quote']}\n\n{a['content']}' for a in host.annotations(doc_id)))
        return host.Response(text, media_type='text/markdown; charset=utf-8', headers={'Content-Disposition': 'attachment; filename=reading-notes.md'})

    @router.get('/api/documents/{doc_id}/annotated.pdf')
    def annotated_pdf(doc_id: str):
        """导出带批注的 PDF 副本：新副本给用户，原文件一个字节都不改。

        只读原 PDF，在内存里生成新字节；本应用的批注数据也不变。
        写不进去的批注不会消失，也不会被硬塞到错误位置——它们仍留在本应用里，
        数量与原因放在 ``X-KanRead-Unplaced`` / ``X-KanRead-Unplaced-Reasons`` 响应头，
        前端据此如实提示。
        """
        meta = host.document(doc_id)
        # 文件缺失 / Zotero 附件变化沿用 document_path 既有的 404 / 409 语义。
        path = host.document_path(meta)
        # 这里直接查原始行而不是复用 host.annotations()：后者会 json.loads(rects)，
        # 遇到坏数据会抛异常让整次导出 500；把坏行交给 build 逐条降级才符合
        # "一条坏数据不影响其余批注"的约定。
        with host.db() as c:
            rows = [dict(row) for row in c.execute(
                'SELECT * FROM annotations WHERE document_id=? AND deleted=0 ORDER BY page,rowid',
                (doc_id,))]
        try:
            payload, report = annotated_export.build(meta, rows, path)
        except annotated_export.ExportError as exc:
            raise host.HTTPException(409, str(exc))
        headers = {
            'Content-Disposition': annotated_export.content_disposition(
                annotated_export.safe_filename(meta.get('name'))),
            'X-KanRead-Placed': str(report['placed']),
            'X-KanRead-Unplaced': str(report['unplaced']),
            # ensure_ascii=True：响应头只能是 ASCII，中文说明留在报告/文档里。
            'X-KanRead-Unplaced-Reasons': host.json.dumps(
                report['unplaced_reasons'], ensure_ascii=True, sort_keys=True),
            # 副本按当前批注现场生成，不能被浏览器或代理缓存成旧内容。
            'Cache-Control': 'no-store',
        }
        return host.Response(payload, media_type='application/pdf', headers=headers)

    return {'annotations': annotations,'create_annotation': create_annotation,'edit_annotation': edit_annotation,'delete_annotation': delete_annotation,'restore_annotation': restore_annotation,'notes': notes,'save_note': save_note,'export': export,'annotated_pdf': annotated_pdf}
