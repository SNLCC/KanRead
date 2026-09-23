"""内置翻译功能。显式宿主服务；不加载任何外部插件。

需求对应的分工：本文件只做 HTTP 契约与权限/页码校验，翻译流程在 ``app/translation.py``，
术语引擎在 ``app/translation_engine.py``，配置与词表在 ``app/translation_config.py``。
"""
from typing import Literal

from pydantic import BaseModel, Field


class PageTranslateBody(BaseModel):
    force: bool = False
    # 只重译指定段落（需求：允许对指定内容重新翻译）。空表示整页。
    only: list[int] = Field(default_factory=list, max_length=2000)
    # 是否连页眉页脚/注释一起翻译（默认不翻，界面会把它们留在原位并标注）。
    include_furniture: bool = False


class DocumentTranslateBody(BaseModel):
    # 不传 pages 就是整篇；传了就是所选页（工作台上的"翻译整篇"用前者）。
    pages: list[int] = Field(default_factory=list, max_length=5000)
    force: bool = False
    request_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')


class SegmentEdit(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=20000)


class QuoteTranslateBody(BaseModel):
    """选文翻译：只发送用户选中的这段文字，不发送整页。"""

    text: str = Field(min_length=1, max_length=12000)
    document_id: str = Field(default='', max_length=64)
    page: int = Field(default=0, ge=0)
    force: bool = False
    request_id: str = Field(default='', pattern=r'^(?:[a-f0-9]{32})?$')


class AnchorBody(BaseModel):
    """把"译文里选中的一小段"映射回原文（句级对齐），供批注定位用。"""

    page: int = Field(ge=1)
    excerpt: str = Field(min_length=1, max_length=12000)
    segment: int | None = Field(default=None, ge=0)


def install(router, host):
    from app import translation, translation_config

    @router.get('/api/translation-settings')
    def translation_settings():
        return translation_config.public(host)

    @router.put('/api/translation-settings')
    def save_translation_settings(body: translation_config.TranslationSettings):
        return translation_config.save(host, body)

    @router.post('/api/translation-settings/probe')
    @host.settings.frozen
    def probe_translation(body: translation_config.ProbeBody):
        """试译一句（不保存设置、不发送任何文献内容），用来验证当前配置真的能翻。"""
        return translation_config.probe(host, body)

    @router.post('/api/translation/quote')
    @host.settings.frozen
    @host.cancellation.cancellable
    def translate_quote(body: QuoteTranslateBody):
        """选文翻译：不落库（选文不是文献内容），但同样走配置好的翻译服务。"""
        from app import translation_engine as engine
        config = translation.translation_config(host)
        pieces = engine.split_segments(body.text) or [{'index': 0, 'text': engine.normalize(body.text)}]
        segment = pieces[0] if len(pieces) == 1 else {'index': 0, 'text': engine.normalize(body.text)}
        values = translation._translate_one(segment, config, translation.engine_profile(host))
        return {'source': segment['text'], 'text': values['text'], 'engine': values['engine'],
                'service': values['service'], 'target_language': config['target_language'], 'saved': False}

    @router.get('/api/documents/{doc_id}/translation/pages/{page}')
    def page_translation(doc_id: str, page: int):
        """读取已保存的译文；没有译文时返回 missing 段落，而不是自动去翻译。"""
        from app import translation_engine as engine
        meta = host.document(doc_id)
        if not 1 <= page <= meta['pages']:
            raise host.HTTPException(400, '页码超出范围')
        path = host.document_path(meta)
        text, origin = translation.read_text(host, doc_id, page, meta, path)
        pieces = engine.split_segments(text)
        config = translation.translation_config(host)
        stored = {row['segment']: row for row in translation.stored_segments(host, doc_id, page)}
        state = translation.page_state(host, doc_id, page)
        # 每段在版面上的位置：界面用它做悬停高亮、按原文分栏摆放，以及区分页眉页脚/注释。
        # **读取路径也必须算**：译文已保存时界面走的是这个 GET，不会再去 POST 翻译，
        # 少算一次就会出现"以前能高亮、现在不能"（用户报的就是这个）。
        boxes = translation.page_boxes(host, doc_id, page, [piece['text'] for piece in pieces], meta, path)
        page_data = host.page_result(doc_id, page) or {}
        layout = translation.decorate_segments(pieces, [box['bbox'] for box in boxes], page_data)
        rows = []
        for piece, box in zip(pieces, boxes):
            current = stored.get(piece['index'])
            hash_value = engine.source_hash(piece['text'])
            if current:
                status = 'edited' if current['edited'] else ('stale' if current['source_hash'] != hash_value else 'saved')
                # 旧版把"术语替换"也存成了译文：这里的 legacy 标记让界面如实说明它不是翻译。
                legacy = not current['service'] or '术语' in (current['service'] or '')
            else:
                status = 'missing'
                legacy = False
            rows.append({'index': piece['index'], 'source': piece['text'], 'source_hash': hash_value,
                         'id': current['id'] if current else '',
                         'text': current['text'] if current else '', 'state': status,
                         'engine': current['engine'] if current else '', 'service': current['service'] if current else '',
                         'edited': bool(current['edited']) if current else False,
                         'legacy': legacy,
                         'kind': piece.get('kind') or 'body',
                         'column': piece.get('column') or 'full',
                         # 超长段落被切开时，切出来的几段带同一个 para：界面把它们显示成一个自然段。
                         'para': piece.get('para') or 0,
                         'bbox': box['bbox'], 'boxes': box['boxes'], 'located': box['located'],
                         # 译文看起来没翻完（服务端默认输出上限的静默截断）：界面提示重译这一段。
                         'truncated': bool(current and current['text']
                                           and engine.looks_unfinished(piece['text'], current['text'])),
                         'updated': current['updated'] if current else ''})
        saved = sum(1 for row in rows if row['text'] and row['state'] != 'stale')
        return {'page': page, 'origin': origin, 'segments': rows, 'saved': saved, 'total': len(rows),
                'located': sum(1 for row in rows if row['located']),
                'furniture': sum(1 for row in rows if row['kind'] != 'body'),
                'page_size': {'width': page_data.get('width'), 'height': page_data.get('height')},
                # 版面参数：界面据此把译文排成"同样的分栏数、与原文一致的字号"（PDF 观感）。
                'columns': layout['columns'], 'body_line_height': layout['body_line_height'],
                'body_font_size': layout['body_font_size'],
                'legacy': sum(1 for row in rows if row['legacy']),
                'service': translation.model_name(translation.engine_profile(host)),
                'target_language': config['target_language'],
                'status': (state or {}).get('status', 'none'), 'error': (state or {}).get('error', ''),
                'translated_at': (state or {}).get('updated', '')}

    @router.post('/api/documents/{doc_id}/translation/pages/{page}')
    @host.settings.frozen
    def translate_page_route(doc_id: str, page: int, body: PageTranslateBody):
        # 页面级翻译是"点一下等结果"的短请求，不接 request_id；整篇翻译才需要停止。
        return translation.translate_page(host, doc_id, page, force=body.force, only=body.only or None,
                                          include_furniture=body.include_furniture)

    @router.post('/api/documents/{doc_id}/translation/pages/{page}/segments/{index}')
    @host.settings.frozen
    def retranslate_segment(doc_id: str, page: int, index: int, body: PageTranslateBody):
        return translation.translate_segment(host, doc_id, page, index)

    @router.patch('/api/documents/{doc_id}/translation')
    def edit_translation(doc_id: str, body: SegmentEdit):
        host.document(doc_id)
        if not body.text.strip():
            raise host.HTTPException(400, '译文不能为空；如需撤销修改请用「恢复机器译文」')
        return translation.edit_segment(host, doc_id, body)

    @router.post('/api/documents/{doc_id}/translation/{segment_id}/restore')
    def restore_translation(doc_id: str, segment_id: str):
        host.document(doc_id)
        return translation.restore_segment(host, doc_id, segment_id)

    @router.post('/api/documents/{doc_id}/translate')
    @host.settings.frozen
    def translate_document(doc_id: str, body: DocumentTranslateBody):
        return translation.start_document(host, doc_id, pages=body.pages or None, force=body.force,
                                          request_id=body.request_id)

    @router.get('/api/documents/{doc_id}/translation/tasks')
    def translation_state(doc_id: str):
        host.document(doc_id)
        return translation.public_state(host, doc_id)

    @router.get('/api/translation/overview')
    def translation_overview(ids: str = ''):
        """工作台每张卡片要显示的翻译状态（一次问清，不要每张卡片发一个请求）。

        状态口径与卡片按钮一一对应：
        ``none`` 没翻译过 →「翻译整篇（后台）」；
        ``partial`` 部分页 →「继续翻译（还有 N 页）」；
        ``done`` 已翻完 →「用当前服务重新翻译整篇」；
        ``running`` 正在翻译 →「翻译中…」。
        """
        wanted = [item for item in (ids or '').split(',') if item][:200]
        result = {}
        for doc_id in wanted:
            try:
                state = translation.document_progress(host, doc_id)
            except host.HTTPException:
                continue
            job = state['job']
            pages, pages_done = state['pages'], state['translated_pages']
            if job.get('running'):
                status = 'running'
            elif job.get('error'):
                status = 'error'
            elif pages_done <= 0:
                status = 'none'
            elif pages_done >= pages:
                status = 'done'
            else:
                status = 'partial'
            result[doc_id] = {'status': status, 'pages': pages, 'translated_pages': pages_done,
                              'segments': state['segments'],
                              'error': job.get('error', ''), 'page': job.get('page', 0)}
        return {'documents': result}

    @router.post('/api/documents/{doc_id}/translation/stop')
    def stop_translation(doc_id: str):
        host.document(doc_id)
        return translation.stop_document(host, doc_id)

    @router.get('/api/documents/{doc_id}/translation/annotations')
    def annotation_translations(doc_id: str):
        """批注页的原文与译文对照（需求"额外处理 1"）：按批注逐条附上重叠的译文。"""
        host.document(doc_id)
        return translation.annotations_with_translation(host, doc_id, host.annotations(doc_id))

    @router.get('/api/documents/{doc_id}/translation/lookup')
    def lookup(doc_id: str, page: int, quote: str):
        """只显示译文时也能对着译文批注：按文字反查它属于哪一段，返回该段原文。"""
        host.document(doc_id)
        if page < 1:
            raise host.HTTPException(400, '页码超出范围')
        return {'matches': translation.lookup_by_quote(host, doc_id, page, quote[:2000])}

    @router.post('/api/documents/{doc_id}/translation/anchor')
    def anchor(doc_id: str, body: AnchorBody):
        """把译文里选中的一小段映射回原文的那几句（并给出它在原页上的框）。

        不再把摘引取成整段：用户反馈过"批注位置不准确、内容依旧是一整段"。
        """
        host.document(doc_id)
        return translation.anchor_selection(host, doc_id, body.page, body.excerpt, body.segment)

    @router.get('/api/translation/languages')
    def languages():
        return {'targets': translation_config.TARGET_LANGUAGES}

    @router.get('/translate-window')
    def translate_window(doc: str = '', page: int = 1):
        """独立译文窗口（多显示器用）。

        做法：这个窗口加载的是**主界面的同一份 HTML 与同一批脚本**（脚本自己去取 `/`，
        因此界面改版时两边不会各差一版），只多一个信号 ``window.__TRANSLATION_WINDOW``：
        翻译功能看到它就进入"只显示译文"的形态，并打开 URL 里指定的文献与页码。
        窗口之间用 BroadcastChannel 同步"看哪一页"，主窗口翻页时它会跟着走。
        """
        payload = {'doc': doc, 'page': max(1, page)}
        asset = host.asset_version()
        html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>译文 · 勘读</title><link rel="stylesheet" href="/static/style.css?v={asset}"></head>
<body><p id="toast" role="status" popover="manual" hidden>正在载入译文窗口…</p>
<script>
// 只带一个信号，界面本体从主页面取：不复制第二份界面代码，改版时不会两边不一致。
window.__ASSET_VERSION={host.json.dumps(asset)};
window.__TRANSLATION_WINDOW={host.json.dumps(payload, ensure_ascii=False)};
(async()=>{{
  try{{
    const response=await fetch('/',{{cache:'no-store'}});
    if(!response.ok)throw new Error('HTTP '+response.status);
    const text=(await response.text()).replace('__ASSET_VERSION__',window.__ASSET_VERSION);
    document.open();document.write(text);document.close();
  }}catch(error){{
    const node=document.getElementById('toast');
    if(node){{node.hidden=false;node.textContent='译文窗口载入失败：'+error.message+'（请回主窗口刷新后重试）';}}
  }}
}})();
</script></body></html>"""
        return host.Response(html, media_type='text/html', headers={'Cache-Control': 'no-cache'})

    return {'translation': translation, 'translation_config': translation_config}
