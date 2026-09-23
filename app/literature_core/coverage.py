"""覆盖协议（Phase 2）：纯函数部分——批次边界、维度命中、覆盖台账。

为什么把这三件事单独放一个模块并且单独测：

1. **批次边界会切断论证**。读原文按预算切成多批，一条限定可能落在某批最后一行、
   它修饰的结论落在下一批第一行。两个批次各自都"合理地"报错，合起来就是一个错误结论。
   这是本项目最隐蔽的失败方式；边界对齐因此做在**切批**那一步（`reading.batches` 的
   `overlap`，重叠必须计入新批预算），本模块负责它的口径与台账。
2. **跨批覆盖必须能合并**。某一批没看到 ≠ 全文没有。合并规则错了，台账就会把
   "这一批没读到"写成"文献里没有"——那是把不确定说成结论。
3. **台账要能解释**。"没找到"必须带上"查过哪里、哪些页读不了"，否则用户无法判断
   是"作者没讨论"还是"系统没读到"。

本模块**不做任何模型调用**：输入是各批次的回报与页面可读状态，输出是确定性的台账。
"""
import hashlib
import re

# 覆盖状态：不同层级只能说不同的话。批次里看到的"没有"**绝不能**升级成"文献里没有"。
FOUND = 'FOUND'
NOT_FOUND_IN_BATCH = 'NOT_FOUND_IN_BATCH'
NOT_FOUND_IN_READ_SCOPE = 'NOT_FOUND_IN_READ_SCOPE'
NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH = 'NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH'
NOT_FOUND_AFTER_FULL_DOCUMENT_REVIEW = 'NOT_FOUND_AFTER_FULL_DOCUMENT_REVIEW'
UNCERTAIN = 'UNCERTAIN'
UNREADABLE = 'UNREADABLE'
NOT_SEARCHABLE = 'NOT_SEARCHABLE'
# "没记账"必须与"没找到"分开：单遍完整读取时原文整体在上下文中，没有逐批回报，
# 维度命中无从记录。把它写成"未发现"就是把"没记账"说成"没有"。
NOT_RECORDED = 'NOT_RECORDED'

# 从弱到强：合并多批结果时取**最强**的那个结论（例如"某批找到"胜过"某批没看到"）。
_STRENGTH = {
    NOT_FOUND_IN_BATCH: 0,
    UNCERTAIN: 1,
    NOT_FOUND_IN_READ_SCOPE: 1,
    NOT_SEARCHABLE: 2,
    UNREADABLE: 2,
    NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH: 3,
    NOT_FOUND_AFTER_FULL_DOCUMENT_REVIEW: 4,
}

# 学术问题通用的一组覆盖维度：先按这组记账，Planner 之后可以补充自己的维度。
# 它们代表"要完整回答这个问题，应当检查哪些方面"，**不是断言原文一定有这些内容**：
# 检查完没有，就如实记成"未发现"。用中文标签是因为它要直接显示给用户。
DEFAULT_DIMENSIONS = (
    ('D1', '概念界定'),
    ('D2', '理论依据'),
    ('D3', '限定条件'),
    ('D4', '反例或异议'),
    ('D5', '作者最终立场'),
    ('D6', '后文修正'),
)
# 台账行数上限：台账是给用户看的一行行结论，维度太多就等于没有结论。
DIMENSION_LIMIT = 8


def readable_scope(coverage):
    """从阅读覆盖记录里算出"哪些页能读、哪些页读不了"，以及**"到底有没有可检索的文字"**。

    三种"读不了"分别记（`read_documents` 已经算好，这里只是汇总，不重新解析 PDF）：

    - `missing_pages`：这一页连原生文字都没有（多半是还没识别的扫描页）；
    - `unparsed_pages`：有原生文字但取不到（解析异常）；
    - `quality_pages`：取到了但疑似乱码。

    它们都属于"**读不到**"，因此不许把"没找到"说成"文献里没有"。

    第 79 轮补上两件事（此前只有一句文档级的 `not_searchable`，说得比事实粗）：

    - `readable` 为**空**与"有一半页读不了"是两码事：前者是"整篇都没有可检索的文字"，
      后者是"读到的那部分里没找到"。前者才能说 `NOT_SEARCHABLE`；
    - 有**任何**一页读得了，就说明本机**有文字可查**，缺口检索因此是"可以查"的——
      这一条让"没做缺口检索"与"根本没有可查文字"在台账里分得开。
    """
    readable = set()
    unreadable = set()
    for doc in (coverage or {}).get('documents') or []:
        readable.update(doc.get('read_pages') or [])
        unreadable.update(doc.get('missing_pages') or [])
        unreadable.update(doc.get('unparsed_pages') or [])
        unreadable.update(doc.get('quality_pages') or [])
    unreadable -= readable          # 同一页两处都出现时以"读到了"为准，不制造自相矛盾的行
    return readable, sorted(unreadable), not readable


def scope_summary(coverage):
    """给报告用的范围口径：能读多少页、读不了多少页、有没有可检索文字。

    与 :func:`readable_scope` 同源，只是把结论写成台账调用方要的形状，
    避免各处再自己拼一遍（拼错就会让"读不了"悄悄变成"读到的那部分没有"）。
    """
    readable, unreadable, no_text = readable_scope(coverage)
    return {'readable_pages': sorted(readable), 'unreadable_pages': unreadable,
        'no_searchable_text': no_text, 'searchable': bool(readable)}


def canonical_quote(quote):
    """逐字核验用的归一化口径：去掉全部空白（与 `original_quote` 同一口径）。

    CRLF/LF、分页产生的换行、空格差异在这里一次性抹平——这正是"同一条证据因抄写空白不同
    而变成两条"的成因，所以证据身份必须建立在这个口径上，而不是原始字符串上。
    """
    return re.sub(r'\s+', '', quote or '')


def evidence_key(document_id, page, quote):
    """一条证据的稳定身份：`document_id + page + 归一化引文` 的摘要。

    为什么不用运行时的 ``[1][2]`` 编号：那些编号在 reduce / 重新绑定之后会变。
    为什么**不**把 chunk/source 编号放进来：`chunks` 的 id 是每次导入新生成的 uuid，
    纳入就等于"重新索引后同一条证据换了一个身份"。同页同一句话在"作者是否说过这句话"
    这个层面本来就是同一件事，因此这里刻意只认内容。

    返回前 16 位十六进制：够短、够区分（同一篇文献同一页内碰撞概率可忽略），便于写进报告。
    """
    raw = f'{document_id}\x1f{page}\x1f{canonical_quote(quote)}'
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def parse_dimensions(raw):
    """把 Planner 给出的覆盖维度变成可信的 ``((编号, 名称), ...)``。

    维度是"**要检查哪些方面**"的清单，不是对原文内容的断言；它只决定"回答完整不完整怎么记账"。
    因此这里的容错口径与逐字证据**相反**：格式不对不该让整次提问失败，也不该让台账凭空少一行——
    给不出可用清单就退回 :data:`DEFAULT_DIMENSIONS`，绝不返回空台账。
    """
    if not isinstance(raw, list):
        return DEFAULT_DIMENSIONS
    result = []
    for item in raw:
        if isinstance(item, dict):
            code, label = item.get('code'), item.get('label')
        elif isinstance(item, str):
            code = label = item
        else:
            continue
        if not isinstance(code, str) or not isinstance(label, str):
            continue
        code, label = code.strip()[:12], label.strip()[:24]
        if not code or not label or any(code == existing for existing, _ in result):
            continue
        result.append((code, label))
        if len(result) >= DIMENSION_LIMIT:
            break
    return tuple(result) or DEFAULT_DIMENSIONS


def dimension_codes(dimensions):
    """台账要按这个顺序逐行输出；编号顺序就是给用户看的顺序。"""
    return tuple(code for code, _ in dimensions or ())


def batch_hits(codes, evidence, reported=None):
    """把一批的证据与命中回报折算成"这一批命中了哪些维度"。

    - 维度由 Planner 预先给出（`D1..Dn`），批次只回报**命中与否**，不自己定义维度；
    - 没有任何证据命中时回报 ``NOT_FOUND_IN_BATCH``——**这是"本批没看到"，不是"文献里没有"**；
    - 判"命中"的口径只有一条：**这一条维度必须落在已经逐字核验过的证据上**。
      证据可以自己声明 `dimension`（精确）；批次自报的 `dimension_hits` 只能把本批已有的
      核验证据**归属**到更多维度上（本批一条核验证据都没有时，自报命中一律不算——
      否则模型只要写一句"命中了"就能让台账记成找到，台账就退化成了模型的自我评价）。
    """
    hits = {code: [] for code in codes}
    for item in evidence or []:
        code = item.get('dimension')
        if code in hits:
            hits[code].append(item.get('evidence_key') or item.get('quote', ''))
    attributed = False
    for code in (reported or []):
        if code not in hits or hits[code]:
            continue
        if not attributed:
            # 只有"本批确实有核验过的证据"时才接受自报归属；证据键按本批全部证据列出。
            keys = [item.get('evidence_key') or item.get('quote', '') for item in evidence or []]
            if not keys:
                break
            attributed = True
        hits[code] = list(keys)
    return {code: (FOUND if keys else NOT_FOUND_IN_BATCH) for code, keys in hits.items()}, hits


def merge_batch_coverage(reports):
    """合并各批次的覆盖回报：**任一**批次找到就算找到；都没找到就说"批次里都没看到"。

    合并规则只有这一条，写成函数是为了它可测：把"某批没看到"误升级成"文献里没有"，
    或者反过来把"某批找到"丢掉，都会让台账变成误导。
    """
    merged = {}
    for report in reports or []:
        for code, state in (report or {}).items():
            current = merged.get(code)
            if current == FOUND or state == FOUND:
                merged[code] = FOUND
                continue
            if current is None or _STRENGTH.get(state, 0) > _STRENGTH.get(current, 0):
                merged[code] = state
    return merged


def coverage_ledger(dimensions, batch_states, evidence_by_dimension=None,
                    readable_pages=None, unreadable_pages=None, not_searchable=False,
                    searched_full_document=False, gap_search_ran=False, examined_scope_complete=True,
                    evidence_pages=None, gap_search_searched=None):
    """生成最终台账：每个维度一行，带状态、证据键与**"查过哪里"的说明**。

    状态升级的唯一合法路径（其余一律保持"本批没看到"）：

    ``NOT_FOUND_IN_BATCH`` → 只有真的做过全文范围检查（`searched_full_document`）
    才允许升到 ``NOT_FOUND_IN_READ_SCOPE`` / ``..._AFTER_INDEXED_TEXT_SEARCH`` /
    ``..._AFTER_FULL_DOCUMENT_REVIEW``；存在读不了的页时，只能停在 ``UNCERTAIN`` 并列出页码——
    这正是本项目的口径：**读不到就明确说读不到，不能宣称"全文没有"**。

    ``examined_scope_complete=False`` 表示**并不是每一批都回报了覆盖维度**（模型没填那个字段）。
    这时"有没有读过"只对回报过的批次成立，因此**一律不许升级**：没记账不是没有。
    把"模型没填字段"读成"文献里没有"，正是这套台账要防的那种错误。

    两个更细的参数（第 79 轮补上，之前各由一条更粗的规则顶着）：

    - `evidence_pages`：``证据键 → 页码``。用来拦住一种**看着有据、实际读不到**的情形：
      某个维度被记成 FOUND，但支撑它的证据**全部来自读不了的页**——那等于拿"读不到的页"
      当"读到了"，只能降回 ``UNCERTAIN``。
    - `gap_search_searched`：缺口检索**是否真的把问题术语拿到文字里查过**（与
      `gap_search_ran` 不同：术语为空、或本机压根没有可查文字时，`ran` 也是假值，
      但那两种情形的含义完全不同——"查了但没查全"与"根本没得查"）。不传时按 `gap_search_ran` 推断，
      因此老调用方的语义一个字都不变。
    """
    ledger = []
    evidence_by_dimension = evidence_by_dimension or {}
    evidence_pages = evidence_pages or {}
    searched = gap_search_ran if gap_search_searched is None else bool(gap_search_searched)
    states = merge_batch_coverage(batch_states)
    unreadable = sorted(set(unreadable_pages or []))
    for code, label in dimensions:
        state = states.get(code, NOT_FOUND_IN_BATCH)
        keys = list(dict.fromkeys(evidence_by_dimension.get(code, [])))
        unreadable_set = set(unreadable)
        # 这一维度的证据**全部**落在读不了的页上：不能说"找到了"（那是拿读不到的页当读到了）。
        evidence_only_on_unreadable = bool(keys) and all(
            evidence_pages.get(key) in unreadable_set for key in keys if key in evidence_pages)
        if (state == FOUND or keys) and not evidence_only_on_unreadable:
            state = FOUND
        elif evidence_only_on_unreadable:
            state = UNCERTAIN
        elif not examined_scope_complete:
            state = NOT_FOUND_IN_BATCH
        elif not_searchable:
            state = NOT_SEARCHABLE
        elif searched_full_document and unreadable:
            # 有页读不了：只能说"在能读的部分里没找到"，并如实列出读不了的页。
            # 这一条要**先于**下面几档判断：只要还有页读不了，"见没见到"就无从谈起——
            # 缺口检索也存在同样的盲区（它查的是本机索引到的文字，那几页根本没有文字）。
            state = UNCERTAIN
        elif searched_full_document and gap_search_ran:
            state = NOT_FOUND_AFTER_FULL_DOCUMENT_REVIEW
        elif searched:
            # 没读完整篇，但**已经在本机索引到的全部文字里查过**：这时的"没找到"比"本批没看到"
            # 强、比"全文没有"弱，正好是这个状态。注意这里用的是"真的查过"（`searched`），
            # 不是"读过全文"——两者是不同的条件，以前混用会让"本批没看到"被误升级。
            state = NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH
        elif searched_full_document and examined_scope_complete:
            # 读过全文、但没做过缺口检索：只能说"**本次阅读范围内**未见到"——
            # 这是这句话字面上的意思，也是它唯一站得住的用法（范围就是实际读过的那些页）。
            # 仍然要求 `examined_scope_complete`：有批次没记账时说"范围内未见到"同样是高估。
            # 最强的那一档（"全文检查未见到"）在这条之前已经拦住了，因此两者不会混。
            state = NOT_FOUND_IN_READ_SCOPE
        row = {'dimension': code, 'label': label, 'status': state, 'evidence': keys}
        if unreadable and state in (UNCERTAIN, NOT_FOUND_IN_READ_SCOPE, NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH,
                                    NOT_FOUND_AFTER_FULL_DOCUMENT_REVIEW, NOT_SEARCHABLE):
            # 任何"没找到"的结论都必须附带读不了的页，否则用户无法判断是"作者没写"还是"没读到"。
            # `NOT_SEARCHABLE` 也一并附上：整篇没有可查文字时，用户更要知道是**哪几页**读不了
            # （此前这一档不带页码，报告里等于只说了一句"没有可检索文字"就完了）。
            row['unreadable_pages'] = unreadable
        if keys and evidence_only_on_unreadable:
            row['reason'] = '支撑这一项的原文所在页读不了，因此不能确认这一项是否被讨论过。'
        if readable_pages is not None:
            row['reviewed_pages'] = len(readable_pages)
        ledger.append(row)
    return ledger


def unrecorded_ledger(dimensions, reason=''):
    """没有逐批记账时的台账：如实说"**这一轮没有逐项记账**"，不写成"没找到"。

    单遍完整读取（原文整体在上下文里）没有分批回报，维度命中无从记录。这种时候唯一诚实的说法是
    "未逐批记账"，而不是"未发现"——后者是把"没记"说成"没有"，正是本模块反复要避免的那类错误。
    """
    return [{'dimension': code, 'label': label, 'status': NOT_RECORDED, 'evidence': [],
             'reason': reason} for code, label in dimensions or ()]


SUFFICIENT = 'SUFFICIENT'
PARTIAL = 'PARTIAL'
MENTION_ONLY = 'MENTION_ONLY'
ABSENT = 'ABSENT'
CONFLICTING = 'CONFLICTING'


def document_answerability(roles, searched=True):
    """这篇文献**对用户这个问题**处在什么状态：`SUFFICIENT / PARTIAL / MENTION_ONLY / ABSENT / CONFLICTING`。

    为什么单独一个概念："检索到相关内容"不等于"这段内容回答了问题"。一条
    "韦伯体现了一种情感主义立场"对"情感主义是什么意思"只是 MENTION——它被检索到、被逐字核验，
    但它没有解释任何东西。把这种情形说成"文献已经回答了"，用户得到的是一份看着有据、实际答非所问
    的回答。

    口径（刻意保守，且**只根据已核验证据的角色**判断，不猜作者意图）：

    - 没有任何证据 → ``ABSENT``；`searched=False`（本轮没查全）时退到 ``UNCERTAIN``：
      "没查到"与"没有"必须分开；
    - 只有 MENTION → ``MENTION_ONLY``；
    - 有 DEFINITION / EXPLANATION → ``SUFFICIENT``（就"文献里有没有可用的界定或解释"而言）；
    - 其余（只有 CHARACTERIZATION / EXAMPLE / CONTRAST / CRITIQUE / QUALIFICATION）→ ``PARTIAL``；
    - ``CONFLICTING`` 需要跨证据的语义比较，**本轮不做**：宁可不说，也不猜。

    这个状态说的是**当前文献**的状态；外部资料永远不能把它改成 SUFFICIENT（见 §34 的口径：
    `document_coverage` 与 `external_grounding` 分开记）。
    """
    counts = {}
    for role in roles or []:
        if isinstance(role, str) and role:
            counts[role] = counts.get(role, 0) + 1
    if not counts:
        return UNCERTAIN if not searched else ABSENT
    if set(counts) == {'MENTION'}:
        return 'MENTION_ONLY'
    if counts.get('DEFINITION') or counts.get('EXPLANATION'):
        return 'SUFFICIENT'
    return 'PARTIAL'


def evidence_roles(evidence):
    """从一组证据里取出角色列表（顺序即证据顺序，便于报告里逐条对应）。"""
    return [item.get('role') for item in evidence or [] if isinstance(item, dict) and item.get('role')]


def ledger_summary(ledger):
    """给报告用的一行摘要：找到/不确定/没找到各几条，以及读不了的页。"""
    counts = {}
    unreadable = set()
    for row in ledger or []:
        counts[row['status']] = counts.get(row['status'], 0) + 1
        unreadable.update(row.get('unreadable_pages') or [])
    return {'total': len(ledger or []), 'by_status': counts, 'unreadable_pages': sorted(unreadable)}
