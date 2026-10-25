"""Internal zotero feature. Explicit host services; no external plugin loading."""

from app import zotero_annotations as _mapping  # 纯映射规则；读取 Zotero 仍走 host.zotero


def _locate_rects(host, doc_id, page, quote):
    """把一条批注的原文定位到这一页的框（返回归一化 0..1 的矩形列表与来源）。

    复用本应用已有的"逐字定位"：Zotero 的坐标只用来确定页码，具体框由我们自己的页面
    文本/OCR 重新匹配。定位不到就返回空列表——不猜、不照抄 Zotero 的坐标。
    """
    from app import citation, page_masks, parsing
    from app.reading_adapter import extracted_page, native_page
    meta = host.document(doc_id)
    path = host.document_path(meta)
    stat = path.stat()
    version = (stat.st_size, stat.st_mtime_ns)
    regions = page_masks.split_regions(page_masks.all_regions(doc_id), page)
    saved = parsing.get(doc_id, page, path)
    if saved is not None:
        text = saved[0]
    else:
        text, _kind = extracted_page(str(path), version, page, True, regions)
    data = host.page_result(doc_id, page)
    lines = parsing.get_boxes(doc_id, page, path)
    boxes, method = citation.locate_page_text(
        text, quote, data.get('width'), data.get('height'), data.get('spans'), lines)
    return [[float(value) for value in box['bbox']] for box in boxes], method


def _migrate_annotations(host, doc_id, commit):
    """迁移 Zotero 批注；commit=False 时只做预览（不写任何内容）。

    迁移是**只读 Zotero**、只写本应用的 annotations 表；重复导入按来源标识去重，
    不会覆盖用户在应用里改过的同一条批注。所有跳过与降级都写进报告。
    """
    meta = host.document(doc_id)
    kind = meta.get('storage_kind') or 'copy'
    if kind not in ('zotero', 'zotero_cache'):
        raise host.HTTPException(400, '这篇文献不是从 Zotero 打开的：请先在「Zotero 文献」页打开附件，再迁移批注。')
    key = _mapping.attachment_key(meta.get('external_key') or '')
    if not key:
        raise host.HTTPException(400, '找不到这篇文献对应的 Zotero 附件标识；请在「Zotero 文献」页重新打开一次该附件。')
    payload = host.zotero.annotation_children(key)
    rows = payload.get('annotations') or []
    with host.db() as c:
        existing = {row[0] for row in c.execute(
            "SELECT external_key FROM annotations WHERE document_id=? AND external_key<>''", (doc_id,))}
    report = {'attachment_key': key, 'mode': 'import' if commit else 'preview',
              'total': len(rows), 'truncated': bool(payload.get('truncated')),
              'imported': 0, 'skipped_existing': 0, 'skipped_type': 0,
              'skipped_page': 0, 'skipped_empty': 0, 'located': 0, 'page_level': 0,
              'unknown_color': 0, 'warnings': [], 'items': []}
    prepared = []
    for row in rows[:_mapping.MAX_IMPORT]:
        data = dict(row.get('data') or {})
        data['key'] = row.get('key') or data.get('key') or ''
        item = _mapping.mapping_of(data, meta.get('pages') or 0)
        entry = {'zotero_key': item.get('zotero_key', ''), 'type': item.get('type', ''),
                 'page': item.get('page'), 'quote': (item.get('quote') or '')[:200],
                 'comment': (item.get('comment') or '')[:200], 'color': item.get('color', ''),
                 'style': item.get('style', ''), 'located': False, 'reason': item.get('reason', '')}
        if not item.get('ok'):
            category = item.get('category') or 'type'
            report['skipped_' + category] = report.get('skipped_' + category, 0) + 1
            report['items'].append(entry)
            continue
        if item['zotero_key'] and item['zotero_key'] in existing:
            report['skipped_existing'] += 1
            entry['reason'] = '这条批注之前已导入过，跳过（不覆盖你在应用里的修改）'
            report['items'].append(entry)
            continue
        rects, method = _locate_rects(host, doc_id, item['page'], item['quote'])
        entry['located'] = bool(rects)
        if rects:
            report['located'] += 1
            entry['reason'] = '按本应用逐字定位到原文'
        else:
            report['page_level'] += 1
            if item['quote']:
                item['style'] = 'page'
                entry['reason'] = '未能在这一页定位到这段文字，已按页级批注导入（位置需你自己核对）'
                report['warnings'].append(f'第 {item["page"]} 页有一条批注未定位到原文，已按页级批注导入。')
        if item.get('unknown_color'):
            report['unknown_color'] += 1
        prepared.append((item, rects, entry))
    if len(rows) > _mapping.MAX_IMPORT:
        report['warnings'].append(f'这篇附件共有 {len(rows)} 条标注，本次只处理前 {_mapping.MAX_IMPORT} 条。')
    if report['truncated']:
        report['warnings'].append('Zotero 返回的批注列表达到读取上限，可能还有未列出的批注；建议分批迁移。')
    if commit:
        prepared.sort(key=lambda triple: (triple[0]['page'], triple[0].get('sort_index') or ''))
        with host.db() as c:
            for item, rects, entry in prepared:
                c.execute('INSERT INTO annotations(id,document_id,page,quote,content,rects,color,'
                          'style,translation_excerpt,external_key) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (host.uuid.uuid4().hex, doc_id, item['page'], item['quote'], item['comment'],
                           host.json.dumps(rects), item['color'],
                           item['style'] if rects else 'page', '', item['zotero_key']))
                report['imported'] += 1
    else:
        report['would_import'] = len(prepared)
    report['items'] = report['items'][:200]
    report['message'] = (
        f'已导入 {report["imported"]} 条批注' if commit else
        f'将导入 {len(prepared)} 条批注（预览，尚未写入）')
    if report['skipped_existing']:
        report['message'] += f'；{report["skipped_existing"]} 条已存在，未重复导入'
    if report['skipped_type'] or report['skipped_page'] or report['skipped_empty']:
        report['message'] += (f'；跳过 {report["skipped_type"] + report["skipped_page"] + report["skipped_empty"]} 条'
                              '（类型不支持 / 无位置 / 内容为空，见明细）')
    return report


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
    @router.post('/api/documents/{doc_id}/zotero-annotations/preview')
    def preview_zotero_annotations(doc_id: str):
        """预览迁移：只读 Zotero 与本机页面，不写任何批注。"""
        return _migrate_annotations(host, doc_id, commit=False)

    @router.post('/api/documents/{doc_id}/zotero-annotations/import')
    def import_zotero_annotations(doc_id: str):
        """把 Zotero 的高亮/下划线/便签迁移成本应用批注；只读 Zotero，不改它。"""
        return _migrate_annotations(host, doc_id, commit=True)

    return {'zotero_settings': zotero_settings,'save_zotero_settings': save_zotero_settings,'zotero_items': zotero_items,'zotero_collections': zotero_collections,'open_zotero': open_zotero}
