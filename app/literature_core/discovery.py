"""语料级（跨文献）两级召回的第一级：**给候选文献排序**（§37/§43 Phase 9）。

这一层只做一件事：决定"预算用尽时先读哪几篇"。它**不是**筛选器，原因写在
`tests/test_reading_quality.py::test_discovery_includes_zero_lexical_hit_documents` 里，
第 70 轮试做"用命中与否决定读多少"时被它挡住并整段撤回：

    跨语言、同义表达、术语不同——"本机没命中"恰恰是最不可靠的信号。
    把它变成"少读"，就是把"没检索到"说成"不相关"。

因此本模块的规则只有两条，而且第二条优先于第一条：

1. **排序可以变，集合不许变**：返回的清单永远是**全部**候选文献（一篇不多、一篇不少），
   只是顺序不同。任何调用方若拿这份顺序去砍掉后面的文献，都是误用。
2. **当前文献永远第一**（用户的问题是在这一篇上问的），其余按
   "命中词数降序 → 名称 → id" 排——完全确定性，因此同一份文献库每次给出同样的顺序。

排序用的信号是**本机检索**（`host.retrieve`，不调模型、不联网），只统计"问题术语出现在哪几篇里"。
命中数为 0 的文献照旧参与阅读，只是排在后面：先把最相关的读完，用户在"继续阅读"时先拿到有用的部分。
"""
import re

# 与问题术语提取同一口径的粗筛：中文连续段、英文单词（≥2 字符）。
# 这里刻意不引入 `chat_feature.query_terms`（那会带来插件层的依赖），
# 两处口径若将来不一致，受影响的是"谁的命中多"，不是"读不读"——排序错了也只是顺序不同。
_TERM = re.compile(r'[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9_-]{1,}')


def terms_of(query, limit=12):
    """从问题里取出可用于比对的候选术语（去重、保序、有上限）。"""
    found = []
    for run in _TERM.findall(query or ''):
        term = run.lower() if run[0].isascii() else run
        if len(term) >= 2 and term not in found:
            found.append(term)
        if len(found) >= limit:
            break
    return found


def document_scores(terms, texts_by_document):
    """每篇文献命中几个术语：`{文档 id: 命中数}`。

    `texts_by_document` 是 `{文档 id: 该文档在本机索引到的全部文字}`——由调用方取数
    （`chunks` + 整页 OCR/校订），本函数只做比对、不碰数据库。
    """
    wanted = [term for term in dict.fromkeys(terms or []) if isinstance(term, str) and len(term) >= 2]
    scores = {}
    for document_id, text in (texts_by_document or {}).items():
        if not isinstance(document_id, str) or not text:
            scores[document_id] = 0
            continue
        lowered = text.lower()
        scores[document_id] = sum(1 for term in wanted if term in lowered)
    return scores


def order_documents(documents, scores, current_id=''):
    """按"当前文献优先 → 命中数降序 → 名称 → id"排序；**返回全部文献**。

    `documents` 是 `[{'id','name',...}]`。这条排序只决定**先读哪几篇**：
    预算用尽时后面的文献进"继续阅读"，不会因为命中少而被判成"不相关"。
    """
    def key(document):
        document_id = document.get('id') or ''
        return (0 if document_id == current_id else 1,
                -(scores or {}).get(document_id, 0),
                str(document.get('name') or ''),
                document_id)
    return sorted(list(documents or []), key=key)


def discovery_report(documents, scores, current_id=''):
    """给报告的排序记录：为什么这样排、每篇命中多少、以及**"这不是筛选"**的那句说明。

    报告必须自己说清"排序 ≠ 筛选"，否则用户看到"只读了 3 篇"会以为是系统判定其余不相关
    （实际是预算用尽，剩下的点继续阅读即可）。
    """
    ordered = order_documents(documents, scores, current_id)
    return {
        'ordered_ids': [document.get('id') for document in ordered],
        'scores': {document.get('id'): (scores or {}).get(document.get('id'), 0) for document in ordered},
        'candidates': len(ordered),
        'hit_documents': sum(1 for document in ordered if (scores or {}).get(document.get('id'), 0) > 0),
        'basis': '本机词面命中数（当前文献优先）',
        'note': '这一层只决定**先读哪几篇**，不决定读不读：命中为 0 的文献同样在阅读范围内，'
                '只是排在后面；预算用完时点「继续阅读」仍会读到它们。',
    }
