"""本地安全网：在**读之前**用本机文字把"相关内容在哪几页"查一遍。

为什么要有它（用户可见的失败方式）：阅读范围是模型判断出来的（local → 指定页码），而模型只知道
问题和标题，不知道正文里哪几页真的在谈这件事。于是一句定义在第 3 页、讨论在第 7–9 页时，
"只看第 3 页"会得到一个**看起来完整、实际上漏掉论证**的回答。反过来，用户看到的是"回答不全"
或"参考只到某一页"。

这一层的口径与模型无关，全部是**确定性**的：

- 只用本机已有的文字（检索片段 `chunks` 与整页校订/OCR `page_edits`），不联网、不调模型、不猜；
- 只在**已经索引到的页**上找，因此"没找到"只能说"在已索引的文字里没找到"——
  这正是台账里 `NOT_FOUND_AFTER_INDEXED_TEXT_SEARCH` 那个状态的含义，两者必须同名同义；
- 命中的页**只增不减**：原有范围一条都不去掉，安全网只做"补进来"；
- 有上限：安全网是保险，不是把一次局部提问变成通读全书（用户已经吃过"动辄读全篇"的亏）。

本模块不碰数据库、不碰模型：输入是 `[(页码, 文字)]` 与若干术语，输出是确定性的命中与补读建议。
取数由调用方负责（`chunks` + `page_edits`）。
"""
import re

# 安全网最多补进来的页数。超过就只补最靠前的几页，并把真实命中数一并报出来。
DEFAULT_LIMIT = 8


def _normalize(text):
    """比对口径：去掉全部空白并转小写。

    与逐字核验同一口径（空白不算差异），另加大小写折叠——英文文献里 "Value Conviction" 与
    "value conviction" 是同一个概念，用户不该因为大小写而漏页。
    """
    return re.sub(r'\s+', '', text or '').lower()


def page_hits(rows, terms):
    """术语在哪些页出现：返回 `{页码: [命中的术语, ...]}`（页码升序、术语按输入顺序）。

    `rows` 是 `[(页码, 文字)]`；同一页出现多次时按页合并。空术语与单字符术语一律忽略：
    "的"这种字会命中每一页，把安全网变成"全书都要读"，与它的目的相反。
    """
    wanted = [term for term in dict.fromkeys(terms or []) if isinstance(term, str) and len(term.strip()) >= 2]
    hits = {}
    if not wanted:
        return hits
    for page, text in rows or []:
        if not isinstance(page, int) or page < 1 or not text:
            continue
        body = _normalize(text)
        matched = [term for term in wanted if _normalize(term) in body]
        if matched:
            hits.setdefault(page, [])
            for term in matched:
                if term not in hits[page]:
                    hits[page].append(term)
    return dict(sorted(hits.items()))


def hit_terms(hits):
    """这次安全网一共查到了哪些术语（用于报告"查过哪些词"）。"""
    found = []
    for terms in (hits or {}).values():
        for term in terms:
            if term not in found:
                found.append(term)
    return found


def missing_terms(terms, hits):
    """一个都没命中的术语：报告要能区分"查了但没找到"与"压根没查"。"""
    found = set(hit_terms(hits))
    return [term for term in dict.fromkeys(terms or []) if term not in found]


def regions(hits, gap=2):
    """把命中页合并成**连续区域**：`31,32,35,36 → 31–36`。

    为什么不能只把命中页交给阅读：一段论证常常"命中页 + 中间一页过渡 + 命中页"，
    只读命中页会把中间那句转折切掉，于是模型看到的是两个孤立的判断。
    合并成区域后读的是**连续原文**，代价是几页过渡页；`gap` 是允许跨过的空档页数
    （默认 2：中间最多两页没命中仍算同一块；`gap=0` 表示严格连续才算一块）。
    """
    pages = sorted({page for page in (hits or {}) if isinstance(page, int)})
    blocks = []
    for page in pages:
        if blocks and page - blocks[-1][1] - 1 <= gap:
            blocks[-1][1] = page
        else:
            blocks.append([page, page])
    return [(start, end) for start, end in blocks]


def region_pages(blocks):
    """区域展开成页码集合（读的是区域里的**每一页**，不是只读命中页）。"""
    return {page for start, end in blocks or [] for page in range(start, end + 1)}


def unread_hits(scope, hits, limit=DEFAULT_LIMIT, gap=2):
    """命中但**不在本轮阅读范围**里的页（合并成区域后展开，页码升序，最多 limit 页）。

    这是"find-all-then-read"的那一步：先读范围，再把漏掉的命中区域补进来。
    顺序按页码，保证结果可复现，也不会因为字典顺序而变。
    """
    read = {page for page in (scope or []) if isinstance(page, int)}
    outside = [page for page in sorted(region_pages(regions(hits, gap))) if page not in read]
    return outside[:limit] if limit else outside


def sweep_report(rows, terms, scope, limit=DEFAULT_LIMIT, source='', gap=2, extra_hits=None):
    """一次安全网的完整记录：查了什么、在哪几页命中、合并成哪些区域、补了哪几页。

    这份记录会直接进覆盖台账，因此**每个数字都是真的**：`scanned_pages` 是这次真的看了多少页文字，
    `hit_pages` 是命中页，`regions` 是合并后的连续区域，`added_pages` 是因此补读的页，
    `missing_terms` 是一个也没命中的术语。用户据此能判断"没找到"是"文献里没有"还是"这次没查"。

    `extra_hits` 是**另一路**召回（第 81 轮的语义宽召回）给出的命中页：它与词面命中**取并集**，
    但两路各自留痕（`lexical_hit_pages` / `semantic_hit_pages` / `hit_pages`），
    这样报告里说得出"这一页是词面命中的，那一页是语义命中的"——合并成一份就再也分不开了。
    语义那一路不提供术语，因此**不影响 `missing_terms`**（那是词面口径的账）。
    """
    hits = page_hits(rows, terms)
    semantic_pages = sorted({page for page in (extra_hits or ()) if isinstance(page, int) and page >= 1})
    merged = {page: list(terms) for page, terms in hits.items()}
    for page in semantic_pages:
        merged.setdefault(page, [])
    blocks = regions(merged, gap)
    added = unread_hits(scope, merged, limit, gap)
    return {
        'source': source,
        'terms': [term for term in dict.fromkeys(terms or []) if isinstance(term, str) and len(term.strip()) >= 2],
        'scanned_pages': len({page for page, _ in rows or [] if isinstance(page, int)}),
        'hit_pages': sorted(merged),
        'lexical_hit_pages': sorted(hits),
        'semantic_hit_pages': semantic_pages,
        'hits': {str(page): terms for page, terms in merged.items()},
        'regions': [list(block) for block in blocks],
        'missing_terms': missing_terms(terms, hits),
        'added_pages': added,
        'truncated': max(0, len([page for page in region_pages(blocks) if page not in set(scope or [])]) - len(added)),
    }
