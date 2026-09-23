"""Internal annotations feature. Explicit host services; no external plugin loading."""

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
    return {'annotations': annotations,'create_annotation': create_annotation,'edit_annotation': edit_annotation,'delete_annotation': delete_annotation,'restore_annotation': restore_annotation,'notes': notes,'save_note': save_note,'export': export}
