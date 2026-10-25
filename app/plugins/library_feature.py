"""Internal library feature. Explicit host services; no external plugin loading."""
import hashlib
import re
import shutil
import time
from pathlib import Path

from app import file_health

# 新建文献用的是 uuid4().hex，所以文献标识只允许 32 位十六进制。relink / snapshot 会用
# 标识拼 data/{id}.pdf，必须先做这个校验再拼：Windows 上 `..\evil` 这类标识会真的穿越出数据目录。
_ID = re.compile(r'^[a-f0-9]{32}$')
MAX_PDF_BYTES = 100 * 1024 * 1024


def _copy_target(host, document_id):
    """应用内副本路径。校验标识 + 确认解析后的父目录就是 DATA，双层防路径穿越。"""
    if not isinstance(document_id, str) or not _ID.fullmatch(document_id):
        raise host.HTTPException(400, '文献标识无效')
    target = host.DATA / f'{document_id}.pdf'
    if target.resolve().parent != host.DATA.resolve():
        raise host.HTTPException(400, '副本路径异常')
    return target


def _save_upload(host, file, target):
    """把上传流写到 target；超过 100 MB 立刻中止（与导入 PDF 的上限一致）。"""
    size = 0
    with target.open('wb') as output:
        while (block := file.file.read(1024 * 1024)):
            size += len(block)
            if size > MAX_PDF_BYTES:
                raise host.HTTPException(413, 'PDF 不能超过 100 MB')
            output.write(block)


def build_index(host, document_id, path):
    """校验 PDF 并生成 (页数, 作者, chunks)。

    import 上传、重新连接（relink）、保存为本机副本（snapshot）共用这一份实现：
    切块口径（1000 字符步长、1200 字符窗、visual 坐标空间）只写一遍，避免"重连"时
    另抄一份后在两处慢慢走偏。已设过"无关区域"的文献重建时同样套用遮罩，
    否则重连会把用户屏蔽掉的页眉页脚又写回索引。
    """
    count = host.pdf_engine.validate(path)
    author = ''
    try:
        author = host.pdf_engine.document_author(path)
    except Exception:
        author = ''
    from app import page_masks
    regions = page_masks.all_regions(document_id)
    chunks = []
    with host.pdf_engine.open_pdf(path) as pdf:
        for index in range(len(pdf)):
            page = pdf[index]
            try:
                data = host.pdf_engine.page_data(page)
                if regions:
                    data = page_masks.filter_page_data(data, page_masks.split_regions(regions, index + 1))
                for span in data['spans']:
                    for start in range(0, len(span['text']), 1000):
                        chunks.append((host.uuid.uuid4().hex, document_id, index + 1,
                                       span['text'][start:start + 1200],
                                       host.json.dumps(span['bbox']), 'visual'))
            finally:
                page.close()
    return count, author, chunks


def write_chunks(connection, document_id, chunks):
    """整体替换一篇文献的原文索引行（先删后插，同一事务里完成）。"""
    connection.execute('DELETE FROM chunks WHERE document_id=?', (document_id,))
    connection.executemany('INSERT INTO chunks(id,document_id,page,text,bbox,bbox_space) VALUES(?,?,?,?,?,?)', chunks)


def _same_bytes(first, second):
    if first.stat().st_size != second.stat().st_size:
        return False

    def digest(path):
        value = hashlib.sha256()
        with path.open('rb') as stream:
            while (block := stream.read(1024 * 1024)):
                value.update(block)
        return value.digest()

    return digest(first) == digest(second)


def _backup_copy(target):
    """给已有副本起一个不重名的备份名：用户重连错文件时，旧副本仍留在数据目录里。"""
    stamp = time.strftime('%Y%m%d-%H%M%S') + f'-{time.time_ns() % 10**9:09d}'
    return target.with_name(f'{target.name}.bak-{stamp}')


def _replace_copy(target, staging):
    """把 staging 换成 ``data/{id}.pdf``，已有不同副本先改名保留，返回备份文件名（无则空串）。

    所有失败路径都把原文件放回：要么保留旧副本，要么回到"没有副本"的干净状态。
    绝不出现"新文件写了一半、旧文件已经删了"这种两头空的状态。
    """
    backup = None
    replaced = False
    try:
        if target.is_file() and not _same_bytes(target, staging):
            backup = _backup_copy(target)
            target.replace(backup)
        if target.is_file():
            # 字节完全相同：不重写文件，原副本与它的指纹都保持不变。
            staging.unlink(missing_ok=True)
        else:
            staging.replace(target)
            replaced = True
        return backup.name if backup else ''
    except Exception:
        if backup is not None and backup.exists():
            backup.replace(target)
        elif replaced:
            target.unlink(missing_ok=True)
        staging.unlink(missing_ok=True)
        raise


def _restore_copy(target, backup_name, had_original):
    """DB 更新失败时把文件换回替换前的状态。"""
    if backup_name:
        backup = target.with_name(backup_name)
        if backup.exists():
            backup.replace(target)
    elif not had_original:
        target.unlink(missing_ok=True)


def _relink_warning(before, after, annotations_out_of_range):
    """重连后的中文提示：页数变了就必须明确说页码可能错位，不粉饰。"""
    notes = []
    if before != after:
        notes.append(f'文献页数由 {before} 页变为 {after} 页：原有批注与笔记的页码可能已错位，请逐条核对后再使用。')
    if annotations_out_of_range:
        notes.append(f'其中 {annotations_out_of_range} 条批注的页码超出了新文件的页数，已无法定位到原文。')
    return ' '.join(notes) or None


def install(router, host):
    @router.get('/api/documents')
    def documents(trash: bool=False):
        with host.db() as c:
            rows = [dict(r) | {'tags': host.json.loads(r['tags'])} for r in c.execute('SELECT * FROM documents WHERE deleted=? ORDER BY created DESC, rowid DESC', (int(trash),))]
        # 附件状态在这里逐篇补上（只 stat，不打开 PDF）。坏文件的记录不能让列表 500：
        # 用户要的正是"列表照常打开，并且看得出哪一篇的附件坏了"。
        for row in rows:
            row['file_state'] = file_health.state_of(row, host.DATA)
        return rows

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
                    # 重新连接时留下的旧副本备份（data/{id}.pdf.bak-*）也属于这篇文献的应用数据，
                    # "彻底删除"不能把它们留在盘上；只删本目录内、命名严格匹配的文件。
                    for backup in host.DATA.glob(f'{doc_id}.pdf.bak-*'):
                        if backup.resolve().parent == host.DATA.resolve() and backup.is_file():
                            backup.unlink(missing_ok=True)
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
            _save_upload(host, file, path)
            count, author, chunks = build_index(host, doc_id, path)
            with host.db() as c:
                c.execute('INSERT INTO documents(id,name,pages,author) VALUES(?,?,?,?)',
                          (doc_id, (file.filename or '未命名.pdf').replace('\\', '/').split('/')[-1], count, author))
                write_chunks(c, doc_id, chunks)
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

    @router.post('/api/documents/{doc_id}/relink')
    @host.settings.frozen
    def relink_document(doc_id: str, file: host.UploadFile):
        """用户选一份新的 PDF 重新连接这份文献。

        原始文件被删除/移动、或 Zotero 链接失效时用这个入口：新文件先写临时路径、
        校验通过后才替换应用内副本；批注/笔记/对话/译文都挂在 document_id 上，一律保留。
        """
        meta = host.document(doc_id)  # 不存在或已在回收站：404
        target = _copy_target(host, doc_id)
        staging = host.DATA / f'.relink-{host.uuid.uuid4().hex}.pdf'
        try:
            _save_upload(host, file, staging)
        except host.HTTPException:
            staging.unlink(missing_ok=True)
            raise
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise host.HTTPException(409, '无法写入临时文件，请检查数据目录是否可写。') from exc
        try:
            page_count, author, chunks = build_index(host, doc_id, staging)
        except Exception as exc:
            staging.unlink(missing_ok=True)
            if isinstance(exc, host.HTTPException):
                raise
            raise host.HTTPException(400, 'PDF 解析失败，请检查文件是否损坏') from exc
        before = int(meta['pages'] or 0)
        had_original = target.is_file()
        backup = ''
        try:
            backup = _replace_copy(target, staging)
            with host.db() as c:
                # 作者可能被用户手工填过：新 PDF 里确实有作者才更新，读不到就保留原值。
                c.execute('UPDATE documents SET pages=?, current_page=MIN(current_page,?), storage_kind=?, '
                          'external_path=?, external_key=?, file_stamp=?, '
                          'author=CASE WHEN ?<>\'\' THEN ? ELSE author END WHERE id=?',
                          (page_count, page_count, 'copy', '', '', '', author, author, doc_id))
                write_chunks(c, doc_id, chunks)
                out_of_range = c.execute('SELECT COUNT(*) FROM annotations WHERE document_id=? AND page>?',
                                         (doc_id, page_count)).fetchone()[0]
        except Exception as exc:
            _restore_copy(target, backup, had_original)
            if isinstance(exc, host.HTTPException):
                raise
            raise host.HTTPException(500, '文件已收到，但索引重建失败；原记录与原有副本未改动。') from exc
        warning = _relink_warning(before, page_count, out_of_range)
        if host.embeddings.enabled():
            try:
                host.embeddings.vectors_for([{'text': c[3]} for c in chunks], host.DATA)
            except Exception:
                extra = '索引已重建；语义索引建立失败，请检查 Embedding 服务，检索时将重试。'
                warning = f'{warning} {extra}' if warning else extra
        row = host.document(doc_id)
        return {**row, 'page_count_before': before, 'page_count_after': page_count,
                'annotations_out_of_range': out_of_range, 'warning': warning,
                'previous_copy_backup': backup,
                'file_state': file_health.state_of(row, host.DATA)}

    @router.post('/api/documents/{doc_id}/snapshot')
    @host.settings.frozen
    def snapshot_document(doc_id: str):
        """把 Zotero 只读链接转成应用内副本（卸载 Zotero 前的"保存到本机"）。

        只复制字节、重建索引，不碰 Zotero 里的原附件；转换后这份文献不再依赖 Zotero 文件位置。
        """
        meta = host.document(doc_id)  # 不存在或已在回收站：404
        # 先校验标识/目标路径，再做任何文件操作：异常标识不能让这段代码走到 Path(external) 之后。
        target = _copy_target(host, doc_id)
        with host.db() as c:
            row = c.execute('SELECT * FROM documents WHERE id=? AND deleted=0', (doc_id,)).fetchone()
        kind = row['storage_kind'] if row is not None else ''
        if kind != 'zotero':
            raise host.HTTPException(400, '只有 Zotero 只读链接需要保存为本机副本；这份文献已经是应用内副本。')
        external = (row['external_path'] or '').strip()
        if not external:
            raise host.HTTPException(409, '这条 Zotero 链接没有记录附件路径，无法保存为本机副本；请先在 Zotero 页面重新连接该附件。')
        source = Path(external)
        if not source.is_file():
            raise host.HTTPException(404, 'Zotero 附件当前不可用（文件不存在）。请确认 Zotero 仍安装、附件没有被移动或删除，然后再试。')
        staging = host.DATA / f'.snapshot-{host.uuid.uuid4().hex}.pdf'
        try:
            shutil.copyfile(source, staging)
            page_count, author, chunks = build_index(host, doc_id, staging)
        except host.HTTPException:
            staging.unlink(missing_ok=True)
            raise
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise host.HTTPException(409, '读取 Zotero 附件失败；请确认文件可读后重试。') from exc
        except Exception as exc:
            staging.unlink(missing_ok=True)
            raise host.HTTPException(400, 'Zotero 附件不是有效的 PDF，无法保存为本机副本。') from exc
        before = int(meta['pages'] or 0)
        had_original = target.is_file()
        backup = ''
        try:
            backup = _replace_copy(target, staging)
            with host.db() as c:
                c.execute('UPDATE documents SET pages=?, current_page=MIN(current_page,?), storage_kind=?, '
                          'external_path=?, external_key=?, file_stamp=?, '
                          'author=CASE WHEN ?<>\'\' THEN ? ELSE author END WHERE id=?',
                          (page_count, page_count, 'copy', '', '', '', author, author, doc_id))
                write_chunks(c, doc_id, chunks)
                out_of_range = c.execute('SELECT COUNT(*) FROM annotations WHERE document_id=? AND page>?',
                                         (doc_id, page_count)).fetchone()[0]
        except Exception as exc:
            _restore_copy(target, backup, had_original)
            if isinstance(exc, host.HTTPException):
                raise
            raise host.HTTPException(500, '副本已复制，但索引重建失败；原记录未改动。') from exc
        warning = _relink_warning(before, page_count, out_of_range)
        row = host.document(doc_id)
        return {**row, 'copied': True, 'page_count_before': before, 'page_count_after': page_count,
                'annotations_out_of_range': out_of_range, 'warning': warning,
                'previous_copy_backup': backup,
                'file_state': file_health.state_of(row, host.DATA)}

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
