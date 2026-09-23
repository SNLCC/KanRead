"""语义宽召回（Phase 3 的后半，§14）：**再找一遍"可能相关"的位置**，不找"最相关的那几个"。

与普通 RAG 的区别是这里的目的完全不同：

- 普通检索问"哪几块最相关"，取 Top-K 就结束；
- 安全网问"**还有没有别的地方也在谈这件事**"——同义表达、换了术语、隔了几页的呼应，
  词面匹配（`sweep.page_hits`）一个都抓不到，而它们恰恰是"回答不全"的常见成因。

三条硬口径（与 `sweep` 同一套，缺一不可）：

1. **只增不减**：语义召回只负责"再补几页"，一条都不去掉；原有范围与词面命中都不受影响。
2. **不猜**：相似度是**阈值 + 相对最优**两条一起判（见 :func:`pick_pages`），不是"取前 K 个"。
   取前 K 个在多页同质的论文里会稳定地补进一堆无关页——那等于把局部提问变回通读全书。
3. **失败即降级**：embedding 没配置、服务报错、维度不一致，都只是"这一路没跑"，
   必须如实记进报告，**不许**因为语义这一路失败就让整次提问失败，也不许假装它跑过。

本模块不碰数据库、不碰模型：向量由调用方给（`app/embeddings.py`），
因此这一层的判断可以完全用假向量来测。
"""
import math

# 相似度下限：低于它一律不算候选。取 0.30 是保守值——语义相似度普遍偏高，
# 阈值定低了会把每一页都补进来（实测过：全部页都落在 0.4 以上时，等于没有筛选）。
MIN_SIMILARITY = 0.30
# 相对最优的折扣：只承认"与最佳候选同档"的页（最佳 0.62 时，低于 0.50 的不算）。
RELATIVE_CUTOFF = 0.80
# 一路最多补几页（与词面安全网共用同一个上限口径，由调用方再截断一次）。
DEFAULT_LIMIT = 8


def cosine(a, b):
    """余弦相似度；任一向量为零向量时返回 0（不抛错，也不当成"完全无关"的特殊值）。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    left = math.sqrt(sum(x * x for x in a))
    right = math.sqrt(sum(y * y for y in b))
    if not left or not right:
        return 0.0
    return dot / (left * right)


def pick_pages(scores, limit=DEFAULT_LIMIT, minimum=MIN_SIMILARITY, cutoff=RELATIVE_CUTOFF):
    """从 `{页码: 相似度}` 里挑出候选页：**阈值 + 相对最优**，页码升序。

    为什么不取前 K 个：一份 40 页的论文里"最相关的 8 页"永远存在，哪怕整篇都无关紧要。
    安全网要的是"确实像在谈这件事"的页，因此两条判据同时成立才算候选：
    绝对相似度过线，且不低于最佳候选的 `cutoff` 倍。**一个都不满足时返回空**——
    "这次没有语义候选"是一个合法结果，不能靠降低标准凑出几页来。
    """
    rows = [(page, float(score)) for page, score in (scores or {}).items()
            if isinstance(page, int) and page >= 1 and math.isfinite(float(score))]
    rows = [(page, score) for page, score in rows if score >= minimum]
    if not rows:
        return []
    best = max(score for _, score in rows)
    floor = max(minimum, best * cutoff)
    chosen = sorted(page for page, score in rows if score >= floor)
    return chosen[:limit] if limit else chosen


def hit_pages_for(rows, query_vector, vectors, limit=DEFAULT_LIMIT, minimum=MIN_SIMILARITY,
                  cutoff=RELATIVE_CUTOFF):
    """把"每页一段文字 + 每段一个向量"折成候选页（页码升序，最多 limit 页）。

    同一页有多段时取**该页最高**的相似度：安全网问的是"这一页谈没谈这件事"，
    页内某一段很像就够了。向量数目与文字数目不一致时按较短的一边处理（不抛错：
    取数那一步少给了一段，不该让整次提问失败）。
    """
    best = {}
    for index, row in enumerate(rows or []):
        if index >= len(vectors or ()):
            break
        page = row[0] if isinstance(row, (tuple, list)) else None
        if not isinstance(page, int) or page < 1:
            continue
        score = cosine(query_vector, vectors[index])
        if score > best.get(page, float('-inf')):
            best[page] = score
    return pick_pages(best, limit=limit, minimum=minimum, cutoff=cutoff)


def find(host_embeddings, data_dir, query, rows, limit=DEFAULT_LIMIT):
    """取数那一层：把 `(页码, 文字)` 拿去算向量并挑出候选页。

    返回 `(候选页, 报告)`；**任何失败都收敛成"这一路没跑"**（`score='跳过'` + 原因），
    绝不向上抛：语义召回是补充手段，不是必要条件。报告里如实写明为什么没跑，
    用户才不会把"语义那一路没跑"读成"没有别的相关页"。
    """
    report = {'source': '语义宽召回', 'pages': [], 'scanned_pages': 0, 'ran': False,
              'top_scores': [], 'note': ''}
    pages = [page for page, text in rows or [] if isinstance(page, int) and text]
    if not pages:
        report['note'] = '本机没有可用于语义比对的文字'
        return [], report
    chunks = [{'text': text, 'document_id': '', 'page': page} for page, text in rows if isinstance(page, int) and text]
    try:
        if not host_embeddings.enabled():
            report['note'] = '未启用 embedding，语义宽召回这一路没有运行'
            return [], report
        vectors = host_embeddings.vectors_for(chunks, data_dir)
        query_vector = host_embeddings.embed([query])[0]
    except Exception as exc:
        # 只记原因，不抛：embedding 服务不可用时整次提问照常进行，只是少了一路召回。
        report['note'] = '语义宽召回未运行：' + (str(exc).strip().splitlines()[0][:160] or exc.__class__.__name__)
        return [], report
    pairs = [(chunk['page'], chunk['text']) for chunk in chunks]
    report['scanned_pages'] = len({page for page, _ in pairs})
    scores = {}
    for (page, _), vector in zip(pairs, vectors):
        score = cosine(query_vector, vector)
        if score > scores.get(page, float('-inf')):
            scores[page] = score
    report['top_scores'] = sorted(((round(score, 4), page) for page, score in scores.items()), reverse=True)[:5]
    pages_found = pick_pages(scores, limit=limit)
    report['pages'] = pages_found
    report['ran'] = True
    if not pages_found:
        report['note'] = '语义比对没有找到达到阈值的页（不等于"没有别处相关"，只说明这一路没找到）'
    return pages_found, report
