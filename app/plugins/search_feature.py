"""Internal search feature. Explicit host services; no external plugin loading."""
import re

# 检索结果要能"落到具体文本位置"：把命中词所在的那一行/那一句作为定位锚点返回，
# 前端据此高亮具体文字，而不是整页或一个陈旧的框。
def matched_excerpt(text, query, limit=240):
    """返回命中词所在的那一行（过长时以命中处为中心截取）；没有命中就给首行。"""
    text = text or ''
    needle = (query or '').strip()
    span = None
    if needle:
        found = re.search(re.escape(needle), text, re.I)
        if found:
            span = (found.start(), found.end())
        else:
            # 查询串跨了标点或换行时，退一步用去掉空白后的逐字匹配。
            squashed = re.sub(r'\s+', '', needle)
            index = re.sub(r'\s+', '', text).lower().find(squashed.lower())
            if index >= 0 and squashed:
                positions = [i for i, char in enumerate(text) if not char.isspace()]
                span = (positions[index], positions[min(index + len(squashed), len(positions)) - 1] + 1)
    if span:
        start, end = span
        left = text.rfind('\n', 0, start)
        right = text.find('\n', end)
        line_start = 0 if left < 0 else left + 1
        line_end = len(text) if right < 0 else right
        excerpt = text[line_start:line_end].strip()
        if len(excerpt) <= limit:
            return excerpt
        centre = (start + end) // 2 - line_start
        half = limit // 2
        return text[line_start + max(0, centre - half):line_start + centre + half].strip()
    return (text.splitlines() or [''])[0].strip()[:limit]


GAP_TYPE_NAMES = {'DOCUMENT_INTERPRETATION': '本文作者的解读', 'CONCEPT_DEFINITION': '概念界定',
    'SCHOLARLY_POSITION': '学术立场', 'HISTORICAL_FACT': '史实', 'BIBLIOGRAPHIC_FACT': '文献事实',
    'CONTESTED_INTERPRETATION': '有争议的解读', 'CURRENT_INFORMATION': '需要时效性的信息',
    'BACKGROUND_EXPLANATION': '背景解释'}


def route_enabled(cfg):
    """联网搜索在隐私权限里是否可用（`off` = 用户明确关掉）。

    缺口这条路的输入来自文献（论断性质与摘引），而"要不要读文献"的判断上一轮已经做过，
    因此这里**不做问题规划**，也**不看** `allow_question`——那个开关管的是"问题能不能用于搜索规划"，
    缺口查证的输入不是新问题。真正决定外发范围的仍是 `web_search.prepare` 里的逐项授权。
    """
    return (cfg or {}).get('permission') != 'off'


def gap_context(body, cfg):
    """缺口查证的**受限**规划输入：只放指认这一处缺口所必需的东西，且逐项尊重既有授权。

    - `question` 始终可发：它是用户自己写的、要求为这一段补依据的那句话；缺口模式下由界面生成。
    - `gap_quote`：报告里那条**已逐字核验**的原文摘引，受"选中原文"同一项授权（`allow_selection`）约束。
      它不是新数据——这一句上一轮已经随原文发给过同一个模型。
    - `claims`：该段自报的论断性质（一个词表内的标签），受"本轮回答草稿"同一项授权（`allow_answer`）约束。
      它影响的是"该找哪类资料"，不是内容本身。

    三项都取不到时返回空 dict，`prepare` 会如实报"没有获准用于搜索的内容"而不是硬发。
    """
    context = {'question': body.query}
    if cfg.get('allow_selection') and body.gap_quote:
        context['selection'] = body.gap_quote
    if cfg.get('allow_answer') and body.gap_type:
        context['answer'] = '这一处缺口的性质：' + GAP_TYPE_NAMES.get(body.gap_type, body.gap_type)
    return {name: value for name, value in context.items() if value}


def install(router, host):
    @router.get('/api/search-settings')
    def search_settings():
        return host.web_search.public()

    @router.put('/api/search-settings')
    def save_search_settings(body: host.web_search.SearchSettings):
        return host.web_search.save(body)

    @router.post('/api/search-plan')
    @host.settings.frozen
    def search_plan(body: host.Search):
        host.document(body.document_id)
        from app.question_route import decide
        with host.db() as c:
            prior=[{'role':r[0],'content':r[1]} for r in c.execute('SELECT role,content FROM messages WHERE document_id=? ORDER BY id DESC LIMIT 4',(body.document_id,))][::-1]
        cfg=host.web_search.config()
        if body.gap_claim:
            # Phase 7（A）：由报告里的"证据缺口"发起的**定向查证**。它不是"要不要读文献"的判断
            # （那个问题上一轮已经回答过），因此**不做问题规划**、也不复用读文献的判断：
            # 它只要一件事——为这一处缺口规划搜索词，然后照旧等用户批准。
            if not route_enabled(cfg):raise host.HTTPException(403,'请先在设置的联网搜索里允许联网')
            return host.web_search.prepare(body, gap_context(body, cfg))
        route=decide(body,prior if cfg.get('allow_history') else []) if cfg.get('allow_question') else {'search':True}
        # 返回空的 token：界面据此**清掉**上一次留下的凭证。不清的话，本轮判为"不需要联网"时，
        # 前端会把上一轮那个已过期的 token 继续发给 /api/chat，用户看到的就是
        # "回答未完成：搜索计划已过期或配置/问题已改变，请重新确认"。
        if not route['search']:return {'skip':True,'review':False,'token':'','queries':[],'reason':route.get('reason','本轮无需联网')}
        cfg = host.web_search.config()
        context = {'question': body.query, 'selection': body.selection}
        with host.db() as c:
            if cfg['allow_history']:
                context['history'] = '\n'.join((r[0] for r in c.execute('SELECT content FROM messages WHERE document_id=? ORDER BY id DESC LIMIT 4', (body.document_id,))))
            if cfg['allow_notes']:
                context['notes'] = '\n'.join((r[0] for r in c.execute('SELECT content FROM notes WHERE document_id=? AND deleted=0 ORDER BY rowid DESC LIMIT 5', (body.document_id,))))
            if cfg['allow_annotations']:
                context['annotations'] = '\n'.join((r[0] for r in c.execute('SELECT content FROM annotations WHERE document_id=? AND deleted=0 ORDER BY rowid DESC LIMIT 5', (body.document_id,))))
        return host.web_search.prepare(body, context)

    @router.post('/api/search')
    @host.settings.frozen
    def search(body: host.Search):
        results = host.retrieve(body)
        # 每条结果带上"命中文本"：前端用它把引用/检索落到具体文字，而不是整页。
        for row in results:
            row['match'] = matched_excerpt(row.get('text'), body.query)
        return results
    return {'search_settings': search_settings,'save_search_settings': save_search_settings,'search_plan': search_plan,'search': search}
