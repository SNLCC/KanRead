"""Phase 7 地基：一个 claim 需要什么等级的证据、该去哪里找（**纯函数，不联网**）。

要防的失败方式：把"模型知道"当成"已经有了足够证据"。模型非常清楚"情感主义是什么意思"，
但这不意味着一个要求可靠学术解释的问题不需要权威依据；反过来，"帮我把这句话说得通俗点"
也不需要为每个词去查专业词条。因此判断不是"要不要联网"，而是：

    claim 的性质（claim_type）× 需要多硬的证据（evidence_standard）
    × 当前文献答到什么程度（document_answerability）× 用户给没给权限（permission）

三条硬口径（都是本项目的既有原则，这里只是把它们写成一个函数）：

1. **外部来源永远不能改写文献覆盖状态**。"本文没有定义 X"不会因为 SEP 上有定义就变成"本文定义了 X"；
   本模块只产出**去哪儿找**的建议，`document_coverage` 由阅读侧负责，两者不得互相覆盖。
2. **权限是许可，不是命令**。用户允许"查找权威资料"不等于每一问都去查；只有
   `document_answerability` 不足以支撑该 claim 时才建议外部来源。
3. **不是什么都要外部证据**。`LIGHT`（解释上下文、通俗化）用现有材料就够；把它也送出去查，
   既慢又把用户的资料外发出去——那是本应用一直避免的事。
"""

# claim 的性质。不同类型要求的来源完全不同（§28）。
CLAIM_TYPES = ('DOCUMENT_INTERPRETATION', 'CONCEPT_DEFINITION', 'SCHOLARLY_POSITION', 'HISTORICAL_FACT',
    'BIBLIOGRAPHIC_FACT', 'CONTESTED_INTERPRETATION', 'CURRENT_INFORMATION', 'BACKGROUND_EXPLANATION')

# 证据等级：模型自己解释就够 / 必须有可核验依据 / 必须达到学术研究级依据。
EVIDENCE_STANDARDS = ('LIGHT', 'GROUNDED', 'SCHOLARLY')

# 来源种类（`MODEL_BACKGROUND` 与 `SYSTEM_INFERENCE` 刻意排在最后：模型是理解器与综合器，
# 不默认是学术证据库）。
SOURCE_KINDS = ('CURRENT_DOCUMENT', 'LIBRARY_DOCUMENT', 'PRIMARY_EXTERNAL_SOURCE', 'SCHOLARLY_REFERENCE',
    'OFFICIAL_SOURCE', 'GENERAL_WEB', 'MODEL_BACKGROUND', 'SYSTEM_INFERENCE')

# 需要联网/外部检索的来源种类。
EXTERNAL_KINDS = ('PRIMARY_EXTERNAL_SOURCE', 'SCHOLARLY_REFERENCE', 'OFFICIAL_SOURCE', 'GENERAL_WEB')

# §29 那张表的实现：先找什么，后找什么。
_PREFERENCE = {
    'DOCUMENT_INTERPRETATION': ('CURRENT_DOCUMENT', 'LIBRARY_DOCUMENT', 'SCHOLARLY_REFERENCE'),
    'CONCEPT_DEFINITION': ('CURRENT_DOCUMENT', 'SCHOLARLY_REFERENCE', 'PRIMARY_EXTERNAL_SOURCE', 'GENERAL_WEB'),
    'SCHOLARLY_POSITION': ('PRIMARY_EXTERNAL_SOURCE', 'SCHOLARLY_REFERENCE', 'CURRENT_DOCUMENT'),
    'HISTORICAL_FACT': ('PRIMARY_EXTERNAL_SOURCE', 'SCHOLARLY_REFERENCE', 'OFFICIAL_SOURCE'),
    'BIBLIOGRAPHIC_FACT': ('OFFICIAL_SOURCE', 'SCHOLARLY_REFERENCE', 'LIBRARY_DOCUMENT'),
    'CONTESTED_INTERPRETATION': ('SCHOLARLY_REFERENCE', 'PRIMARY_EXTERNAL_SOURCE', 'LIBRARY_DOCUMENT'),
    'CURRENT_INFORMATION': ('OFFICIAL_SOURCE', 'GENERAL_WEB'),
    'BACKGROUND_EXPLANATION': ('CURRENT_DOCUMENT', 'MODEL_BACKGROUND'),
}

# 当前文献"答到什么程度"是否已足以支撑这个 claim。SUFFICIENT 只对**文献解释类**问题成立：
# "作者在本文怎么理解 X"当然由原文说了算；"X 学界一般怎么界定"不能只靠这一篇。
_DOCUMENT_ENOUGH = {'DOCUMENT_INTERPRETATION', 'BACKGROUND_EXPLANATION'}

# 每条 claim 至少需要多硬的证据（**不是**模型说了算的那一档）。
#
# 为什么要有这个下限：`standard` 一旦由模型自己填，最省事的回答方式就是把所有论断都写成 `LIGHT`，
# 于是"不需要外部依据"变成模型的自我豁免——正是本模块开头要防的那种失败。下限让模型**只能把
# 标准往上提、不能往下压**（写错、写漏时也按更严的那一档走），与逐字证据"宁可少说"同一条口径。
#
# 下限本身刻意保守（不是"凡论文必学术级"）：
# - 论文解释、背景解释要的是**可核验依据**：能指回原文，不靠印象；
# - 定义、学派立场、史实、文献事实、争议解读都要**学术研究级**依据；
# - 时效性信息要**官方来源**级依据。
# `LIGHT` 只留给"这一段本来就被标成**未经核验的背景知识**"的情形——判定规则写在
# `resolve_standard` 里，它决定了模型**压不低**这个下限。
_MINIMUM_STANDARD = {
    'DOCUMENT_INTERPRETATION': 'GROUNDED',
    'BACKGROUND_EXPLANATION': 'GROUNDED',
    'CONCEPT_DEFINITION': 'SCHOLARLY',
    'SCHOLARLY_POSITION': 'SCHOLARLY',
    'HISTORICAL_FACT': 'SCHOLARLY',
    'BIBLIOGRAPHIC_FACT': 'SCHOLARLY',
    'CONTESTED_INTERPRETATION': 'SCHOLARLY',
    'CURRENT_INFORMATION': 'SCHOLARLY',
}

_LEVEL_ORDER = {'LIGHT': 0, 'GROUNDED': 1, 'SCHOLARLY': 2}


def claim_type(value):
    """把模型给的 claim 性质归一化：词表外的值一律退回最保守的那一档，不抛错、也不当成"随便找"。"""
    return value if value in CLAIM_TYPES else 'BACKGROUND_EXPLANATION'


def minimum_standard(claim):
    """这条 claim 的最低证据等级；没见过的类型按最严的那一档走（不许靠写错类型换轻标准）。"""
    return _MINIMUM_STANDARD.get(claim_type(claim), 'SCHOLARLY')


def resolve_standard(claim, declared, verdict=''):
    """定下这条 claim 实际按多严的标准找证据：**模型只能往上提，不能往下压**。

    - 模型给了一个合法的、比下限更严的等级 → 采纳它（模型确实可能更需要学术级依据）；
    - 模型给得比下限低、给了词表外的值、或根本没给 → 一律取下限；
    - 唯一的例外是"这一段本来就被标成**未经核验的背景知识**"（`verdict='background'`，只在用户
      明确允许模型背景知识时才会出现）：那一段已经如实标着"未核验"，本来就只需要解释、不需要依据，
      因此可以按 `LIGHT` 走。除此之外 `LIGHT` **不是**模型可以给自己发的豁免——一个把每条论断都
      写成"只需解释现有材料"的回答，恰恰是本模块要防的那种情形。
    """
    declared = declared if declared in EVIDENCE_STANDARDS else ''
    floor = minimum_standard(claim)
    if declared and _LEVEL_ORDER[declared] >= _LEVEL_ORDER[floor]:
        return declared
    if verdict == 'background' and _LEVEL_ORDER[floor] > _LEVEL_ORDER['LIGHT']:
        return 'LIGHT'
    return floor


def source_order(claim_type):
    """这条 claim 找证据的顺序（文献内与外部混在一起，供调用方同一条规则地取用）。

    `source_plan` 内部按"文献内 / 外部"拆开它，接线层想知道"没获授权时会缺哪一类"时也用它——
    两处共用同一张表，避免"建议用 A、实际去找 B"。
    """
    return _PREFERENCE[claim_type if claim_type in CLAIM_TYPES else 'BACKGROUND_EXPLANATION']


def source_plan(claim_type, standard='GROUNDED', answerability=None, allowed=()):
    """给出一条 claim 的来源计划：先看哪里、需不需要外部、以及**为什么**。

    返回 `{'claim_type','standard','sources':[...],'needs_external':bool,'reason':str}`。
    - `allowed` 是用户给的外部来源权限（`EXTERNAL_KINDS` 的子集）；没给权限时**只**产出文献内建议，
      并把原因写清楚（"需要外部资料但本轮没有授权"），而不是悄悄去找；
    - `LIGHT` 等级不要求外部：现有材料与模型解释足够；
    - `answerability='SUFFICIENT'` 且属于文献解释类问题时，外部来源**不必要**（有权威资料也不改口径）。
    """
    claim = claim_type if claim_type in CLAIM_TYPES else 'BACKGROUND_EXPLANATION'
    level = standard if standard in EVIDENCE_STANDARDS else 'GROUNDED'
    order = _PREFERENCE[claim]
    permitted = tuple(allowed or ())
    sources = [kind for kind in order if kind not in EXTERNAL_KINDS]
    wanted = []
    if level != 'LIGHT':
        # 文献已足够回答（且这是文献解释类问题）时不需要外部；其余情况按偏好表找外部。
        enough = answerability == 'SUFFICIENT' and claim in _DOCUMENT_ENOUGH
        if not enough:
            wanted = [kind for kind in order if kind in EXTERNAL_KINDS]
    external = [kind for kind in wanted if kind in permitted]
    if external:
        sources.extend(external)
    reason = _reason(claim, level, answerability, wanted, external)
    return {'claim_type': claim, 'standard': level, 'sources': sources,
        'needs_external': bool(external), 'blocked_external': [k for k in wanted if k not in permitted],
        'reason': reason}


def _reason(claim, level, answerability, wanted, external):
    if level == 'LIGHT':
        return '只需解释现有材料，不需要外部依据'
    if not wanted:
        if answerability == 'SUFFICIENT' and claim in _DOCUMENT_ENOUGH:
            return '当前文献已给出界定或解释，外部资料不必要（有也不改变"文献说了什么"）'
        return '按该 claim 的性质，优先使用当前文献与文献库'
    if not external:
        return '需要外部资料，但本轮没有获得相应授权；不改动文献覆盖状态，只按现有材料作答'
    if answerability in ('MENTION_ONLY', 'PARTIAL', 'ABSENT', None):
        return f'当前文献对这个问题是 {answerability or "未知"}，按 claim 性质需要外部依据'
    return '按 claim 性质需要外部依据'


def external_blocked(plan):
    """这次"想找但没权限"的来源种类（报告里要如实说出来，不能假装查过）。"""
    return list((plan or {}).get('blocked_external') or [])
