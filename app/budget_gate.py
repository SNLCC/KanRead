"""发送前预算闸门：在真正读取原文之前，把"这一轮大概要读多少"告诉用户。

为什么放在"读之前"而不是"读的时候"：读原文会立刻产生模型调用（长文献是几十次），
等发现太贵再停已经花掉了钱和时间。这里只看**本机已索引的数据**做估算，不调用模型、
不写数据库、不改读取范围；用户确认后才走原有阅读路径。

估算口径（不假装精确）：
- 页数按**本轮计划范围**算（local=计划页；discovery=语料内各文献页数之和；其余=整篇页数）；
- token 用 `chunks` 里该范围的字符总数 × 抽样文本的"字符→token"比例；
  抽不到文本时退回每页 1500 token 的保守估计。抽样比例用的是核心的 `reading.cost`，
  它本来就是"装不装得下"的估算器，不是计费口径。
- 阈值是常量（页数 40 / token 60000），够挡住"整本书直接跑"的大部分情况；
  以后要可配置时改这里即可，不要在前端再写一份。
"""
THRESHOLDS = {'pages': 40, 'tokens': 60000}
PAGE_FALLBACK_TOKENS = 1500
SAMPLE_LIMIT = 4000


def _is_cjk(char):
    code = ord(char)
    return (0x3040 <= code <= 0x30ff or 0x3400 <= code <= 0x9fff
            or 0xf900 <= code <= 0xfaff or 0xac00 <= code <= 0xd7af)


def _sample_ratio(sample):
    """抽样文本里"每字符约多少 token"。

    这里刻意不用 `reading.cost` 的容量口径：那是**保守上界**（非 CJK 也按 1 token/字符），
    用来防止"装不下"；拿它当用户看到的费用预告会把英文文献高估约 3–4 倍，于是正常提问
    也会被要求确认，闸门很快就会被用户关掉。这里改按字符类别给一个接近实际、仍偏保守的
    比例：CJK 1.5 token/字，其余 0.3 token/字符（中英混排时按实际比例加权）。
    """
    if not sample:
        return 0.0
    cjk = sum(1 for char in sample if _is_cjk(char))
    other = len(sample) - cjk
    return (cjk * 1.5 + other * 0.3) / len(sample)


def _scope_documents(host, body):
    ids = set(host.scope_ids(body))
    return [doc for doc in host.documents() if doc['id'] in ids]


def _indexed_text_stats(host, document_ids):
    """返回 (字符总数, 抽样文本)。只读 chunks；没有索引文本时返回 (0, '')。"""
    if not document_ids:
        return 0, ''
    placeholders = ','.join('?' for _ in document_ids)
    with host.db() as connection:
        row = connection.execute(
            f'SELECT SUM(LENGTH(text)) FROM chunks WHERE document_id IN ({placeholders})',
            tuple(document_ids)).fetchone()
        total = int(row[0] or 0)
        samples = connection.execute(
            f'SELECT text FROM chunks WHERE document_id IN ({placeholders}) '
            'AND LENGTH(text)>0 ORDER BY LENGTH(text) DESC LIMIT 3',
            tuple(document_ids)).fetchall()
    sample = '\n'.join(row[0] for row in samples if row[0])[:SAMPLE_LIMIT]
    return total, sample


def estimate(host, body, plan):
    """估算本轮预计读取的页数与 token。只读，不产生任何副作用。"""
    documents = _scope_documents(host, body)
    by_id = {doc['id']: doc for doc in documents}
    current = by_id.get(body.document_id)
    if current is None:
        current = host.document(body.document_id)
    if plan.breadth == 'local':
        pages = len(plan.pages or ())
    elif plan.breadth == 'discovery':
        pages = sum(int(doc.get('pages') or 0) for doc in documents)
    else:
        pages = int(current.get('pages') or 0)
    characters, sample = _indexed_text_stats(host, [doc['id'] for doc in documents])
    ratio = _sample_ratio(sample)
    if sample and characters and ratio > 0:
        tokens = int(characters * ratio)
    elif characters:
        tokens = int(characters / 2)
    else:
        tokens = 0
    if tokens <= 0:
        tokens = pages * PAGE_FALLBACK_TOKENS
        method = '没有可用的已索引文本：按每页约 1500 token 保守估算'
    else:
        method = '按已索引文本的字符总量与抽样比例估算（CJK 1.5 token/字、其余 0.3 token/字符）'
    exceeded = pages > THRESHOLDS['pages'] or tokens > THRESHOLDS['tokens']
    return {
        'pages': pages,
        'tokens': tokens,
        'documents': len(documents),
        'thresholds': dict(THRESHOLDS),
        'exceeded': exceeded,
        'estimated': True,
        'method': method,
        'note': '这是估算：实际读取范围与用量以本轮为准；确认后才会开始读取。',
    }


def check(host, body, plan):
    """需要确认时返回估算结果，否则返回 None（已确认 / 未超阈值 / 非文献路径）。"""
    if getattr(body, 'confirm_budget', False):
        return None
    result = estimate(host, body, plan)
    return result if result['exceeded'] else None
