"""陷阱型基准（Phase 0）：量化"读到的东西有没有在链路中丢掉"。

为什么单独做这个基准：普通问答 benchmark 测不出本项目的核心风险。风险不是"检索没找到"，
而是**全文都读过了、关键的一句话却在分批/压缩/合成中消失**。所以这里用人工构造的陷阱文献：
每篇里埋一句"决定答案对错"的话，然后用假模型（不联网、不调用任何付费服务）跑真实链路，
逐环节核对那句话还在不在。

六类陷阱（A–F）对应六种真实失败方式：

- A 后文限制：前文给出明确观点，后文另一位置追加关键限制 → Qualification Recall
- B 脚注例外：正文提出原则，脚注给出例外 → Evidence Recall
- C 转述与自我立场：前文详细转述他人观点，后文才表明自己否定 → Author-position Accuracy
- D 同义异词：关键结论用另一套术语表达（"价值确信" vs "conviction"）→ Semantic Recall
- E 批边界陷阱：限制在某一批的**末尾**、它限定的结论在下一批的**开头** → 分批是否割裂论证
- F 压缩丢失：一页里埋一条短小但决定性的限定，与全文主旨关系很弱 → Evidence Retention Recall

每一项都回答同一个问题的不同侧面：**全文读到 ≠ 最终答案用上。**

用法（仓库根目录）::

    .venv\\Scripts\\python.exe -m scripts.bench_trap_recall

全部是合成文献 + 假模型：不联网、不读用户文献、不改动任何用户数据。输出的是"结构"指标，
不是对某个平台计费口径的复现。
"""
import json
import os
import re
import shutil
import sys
import time

# 陷阱句：短、唯一、可逐字匹配。假模型只会**照抄它在材料里看见的**陷阱句，
# 所以"某句没进最终结果"只能是链路把它丢了，不会与模型能力混在一起。
TRAPS = {
    "late_limit": "However, this conclusion holds only for the urban sample and cannot be generalised to rural regions.",
    "footnote_exception": "Footnote 12: the exception applies whenever the household lacks formal registration.",
    "attribution": "This paragraph summarises the position of March and Olsen rather than the author's own view.",
    "author_stance": "The author disagrees with the account summarised above and treats it as descriptively false.",
    "synonym": "The same idea is expressed elsewhere as conviction: acting on what one holds to be true.",
    "boundary_limit": "The restriction introduced here applies to every conclusion in the following section.",
    "reduce_loss": "One qualification matters more than the rest: the effect disappears when the sample is trimmed.",
    "topic_conclusion": "The following section states the main conclusion of the study.",
}

# 取文会把直引号换成弯引号（`'` → `’`）。这只是排版差异，判"这句话在不在材料里、模型照抄的是哪一句"
# 时必须折平，否则会把**看得见**的句子判成没看见（实测：`attribution` 因此长期显示为 MISS，
# 排查时被误读成"分批把它切掉了"）。注意：折平只用于**基准自己的比对**；
# 假模型引用时必须照抄材料的实际字符（见 material_span），否则逐字核验会正确地拒掉它。
_QUOTE_FOLD = str.maketrans({'\u2019': "'", '\u2018': "'", '\u201c': '"', '\u201d': '"'})


def fold_quotes(text):
    return (text or '').translate(_QUOTE_FOLD)


def normalize_text(text):
    return re.sub(r'\s+', '', fold_quotes(text))


def material_span(material, sentence):
    """这句话在材料里的**实际字符片段**（假模型照抄它）；找不到返回 None。"""
    haystack = normalize_text(material)
    needle = normalize_text(sentence)
    offset = haystack.find(needle)
    if offset < 0 or not needle:
        return None
    positions = [index for index, char in enumerate(material) if not char.isspace()]
    return material[positions[offset]:positions[offset + len(needle) - 1] + 1]

PRIMARY = "value_conviction"
DIMENSIONS = [
    ("D1", "概念界定", "definition of value conviction"),
    ("D2", "理论依据", "theoretical grounds for value conviction"),
    ("D3", "限定条件", "qualifications and limits"),
    ("D4", "作者最终立场", "the author's own final position"),
    ("D5", "反例", "counterexamples"),
]


def filler(page, index):
    """普通正文：与陷阱无关但"像论文"。它决定陷阱句在批次的哪个位置。"""
    return (f"Page {page}. [{index:05d}] The argument proceeds by distinguishing levels of analysis and by "
            f"specifying the conditions under which the claim is expected to hold. The discussion reviews the "
            f"relevant literature, reports the sampling frame, and notes the limits of the measurement "
            f"instrument used in the third wave of the survey.")


def build_document(padding_pages=0, boundary_gap=0):
    """构造一篇带陷阱的合成论文；返回 (pages, 陷阱名→页码)。

    陷阱句的位置是关键：`boundary_limit` 要落在某一批的**末尾**，它限定的结论在下一批的**开头**。
    页码由最终顺序现算（不靠插入时推测），因为下面会补大量填充页。
    """
    index = 0
    pages = []

    def emit(*lines):
        pages.append("\n".join(lines))

    emit("The study introduces value conviction and argues that it explains compliance under uncertainty.",
         "The main claim is stated here and defended throughout the paper.")
    emit("The theoretical grounds combine a theory of action with evidence on organisational routines.",
         "Neither source alone is treated as sufficient.")
    emit(TRAPS["late_limit"])                      # A 后文限制
    for _ in range(2):
        emit(filler(0, index)); index += 1
    emit(TRAPS["topic_conclusion"], filler(0, index)); index += 1   # 结论开场（独立成句）
    emit(TRAPS["boundary_limit"])                  # E 批边界：紧跟结论之后
    for _ in range(boundary_gap):
        emit(filler(0, index)); index += 1
    emit(TRAPS["footnote_exception"])              # B 脚注例外
    emit(TRAPS["attribution"])                     # C 转述
    emit(TRAPS["author_stance"])                   # C 自我立场
    emit(TRAPS["synonym"])                         # D 同义异词
    emit(TRAPS["reduce_loss"])                     # F 压缩丢失
    for _ in range(padding_pages):
        emit(filler(0, index)); index += 1

    numbered = [re.sub(r'^Page [^.]*\.', f'Page {number}.', text) if text.startswith('Page') else
                f'Page {number}. {text}' for number, text in enumerate(pages, 1)]
    traps_page = {}
    for number, text in enumerate(numbered, 1):
        for name, sentence in TRAPS.items():
            if sentence in text:
                traps_page.setdefault(name, []).append(number)
        if "introduces value conviction" in text:
            traps_page.setdefault("main_claim", []).append(number)
    return numbered, traps_page


def same_text(haystack, needle):
    """去掉空白、折平引号后比较：与链路里逐字核验同一口径（除了引号那层排版差异）。

    必须这样比：PDF 取文会把一句话在排版换行处断开、把直引号换成弯引号，用原始子串判
    "模型看没看见这句话"，会把**看得到**的句子判成没看到。
    """
    return normalize_text(needle) in normalize_text(haystack)


def source_block_of(material, sentence):
    """这条句子出现在 material 的哪个编号块里；找不到返回 None。

    假模型必须**像真模型一样**只能引用它看见的那个编号：早前这里图省事写成"材料里最后一个编号"，
    于是每一句摘引都被逐字核验正确地判成"不属于这个编号"、整条被丢掉，基准的 0/8 因此测的是
    "假模型编号写错"，而不是"链路把证据压没了"——这是基准自己的缺陷，不是被测代码的表现。
    """
    for number, block in re.findall(r'\[(\d+)\][^\n]*\n(.*?)(?=\n\n\[|\Z)', material, re.S):
        if same_text(block, sentence):
            return int(number)
    return None


def respond(document_text, traps_page, material, system, calls, batches_read):
    """假模型：只引用它在材料里**真的看见**的陷阱句，并给出本批的覆盖状态。"""
    trap_list = [(name, sentence) for name, sentence in TRAPS.items() if same_text(material, sentence)]
    if "READING_BATCH" in system:
        calls.append("READING_BATCH")
        batches_read.append([name for name, _ in trap_list])
        items = []
        for _, sentence in trap_list:
            number = source_block_of(material, sentence)
            quote = material_span(material, sentence)
            if number is not None and quote is not None:
                items.append({"source": number, "quote": quote})
        coverage = {}
        for code, _, _ in DIMENSIONS:
            coverage[code] = "FOUND" if any(name_for(code, name) for name, _ in trap_list) else "NOT_FOUND_IN_BATCH"
        return json.dumps({
            "analysis": "本批记录了论证、限定与联系。",
            "evidence": items,
            "no_relevant_evidence": not items,
            "coverage": coverage,
        })
    if "READING_REDUCE" in system:
        calls.append("READING_REDUCE")
        # 材料前面有"用户问题/选文"这一行前缀，因此从第一个 `[` 开始解析（与真实调用一致）。
        pack = json.loads(material[material.index("["):])
        assert pack, "合并层不该收到空的记录清单"
        # 合并层现在**只合并分析**：逐字证据由证据银行原样搬运，不再经过模型的手。
        # 这个假模型因此也只回分析——若证据仍然丢了，责任只可能在链路，不可能在这个假模型。
        return json.dumps({"analysis": "合并后的阅读记录。"})
    if "READING_AUDIT" in system:
        calls.append("READING_AUDIT")
        row = re.search(r'\[(\d+)\][^\n]*\n(.{10,80})', material)
        if not row:
            return json.dumps({"claims": []})
        return json.dumps({"claims": [{"claim": 1, "verdict": "supported", "reason": "ok",
                                       "evidence": [{"source": int(row.group(1)),
                                                     "quote": row.group(2).split("\n")[0][:60]}]}]})
    if "QUESTION_ROUTE" in system:
        calls.append("QUESTION_ROUTE")
        return json.dumps({"document": True, "search": False, "reason": "整篇论述", "reply": "",
                           "reading": "document", "confidence": 0.9, "pages": []})
    if "READING_REREAD" in system:
        calls.append("READING_REREAD")
        return json.dumps({"sources": [], "read": [], "reason": "证据已足够"})
    calls.append("OTHER")
    return "作者把价值确信理解为在不确定条件下按自己所信行动，并给出限定与反例 [1]。"


def name_for(code, name):
    """把维度代码映射回可判定的陷阱名（假模型用它决定该维度是否命中）。"""
    return {
        "D1": name in ("late_limit", "main_claim"),
        "D2": name in ("attribution", "main_claim"),
        "D3": name in ("late_limit", "boundary_limit", "reduce_loss", "footnote_exception"),
        "D4": name in ("author_stance",),
        "D5": name in ("footnote_exception", "reduce_loss"),
    }.get(code, False)


def cleanup_stale_folders(prefix, keep_seconds=600):
    """删掉**上一次跑**留下的临时目录（本仓库根目录下的 `.test-bench-*`）。

    为什么不只删自己的：本进程那个目录往往删不掉——应用还持有 sqlite 的句柄，Windows 直接
    拒绝删除——于是每跑一次就多留一个，本机实测在仓库根目录堆了 **87 个** `.test-bench-trap-*`。
    所以改成"下一次运行时先扫掉上一批"。只删**十分钟前**建的：同一台机器上万一有并发的
    另一轮基准在跑，不能把它的工作目录抽掉。
    """
    root = os.getcwd()
    now = time.time()
    for name in os.listdir(root):
        if not name.startswith(prefix) or name == f"{prefix}{os.getpid()}":
            continue
        path = os.path.join(root, name)
        try:
            if not os.path.isdir(path) or now - os.path.getmtime(path) < keep_seconds:
                continue
        except OSError:
            continue
        shutil.rmtree(path, ignore_errors=True)


def run(padding_pages, boundary_gap, label):
    """跑一个用例；返回可断言的结果字典。

    **跑完必须把进程环境恢复原样**：本函数也被 pytest 调用（陷阱基准同时是一条基线测试），
    如果它把 READER_DATA_DIR / READER_API_BASE 留在进程里，后面别的测试就会在**它的数据目录**里跑
    ——实测确实如此（另一个翻译用例因此读到"已翻过"的旧记录而失败）。这是测试卫生问题，
    不是被测代码的问题，所以修在这里。
    """
    cleanup_stale_folders(".test-bench-trap-")
    folder = os.path.join(os.getcwd(), f'.test-bench-trap-{os.getpid()}')
    os.makedirs(folder, exist_ok=True)
    previous = {name: os.environ.get(name) for name in ("READER_DATA_DIR", "READER_API_BASE")}
    os.environ["READER_DATA_DIR"] = folder
    os.environ["READER_API_BASE"] = "http://localhost:9999/v1"
    sys.path.insert(0, os.getcwd())

    import httpx
    from fastapi.testclient import TestClient
    from tests.test_app import pdf_bytes

    pages, traps_page = build_document(padding_pages, boundary_gap)
    document_text = "\n".join(pages)
    calls = []
    batches_read = []

    class Fake:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def close(self): pass
        def post(self, url, **kw):
            payload = kw["json"]
            system = " ".join(m["content"] for m in payload["messages"] if m["role"] == "system")
            material = payload["messages"][-1]["content"]
            if isinstance(material, list):
                material = " ".join(p.get("text", "") for p in material if isinstance(p, dict))
            text = respond(document_text, traps_page, material, system, calls, batches_read)

            class R:
                def raise_for_status(self): pass
                def json(self):
                    return {"choices": [{"message": {"content": text}}],
                            "usage": {"prompt_tokens": 0, "completion_tokens": 0}}
            return R()

    httpx.Client = Fake
    import importlib
    import app.main as main
    importlib.reload(main)

    started = time.perf_counter()
    with TestClient(main.app) as client:
        # 把容量压到会真的分成多批（默认 65536 时整篇一次装下，就测不到分批与压缩）。
        saved = client.put("/api/settings", json={"chat": {"enabled": True, "base_url": "http://localhost:9999/v1",
            "model": "fake", "context_window": 8192, "output_reserve": 512}, "embedding": {}})
        assert saved.status_code == 200, saved.text
        created = client.post("/api/documents", files={"file": (f"{label}.pdf", pdf_bytes(pages), "application/pdf")})
        assert created.status_code == 200, created.text
        doc = created.json()
        result = client.post("/api/chat", json={"document_id": doc["id"],
                                                "query": "请完整梳理作者关于价值确信的全部论述，包括限定与反例"})
    elapsed = time.perf_counter() - started
    # 环境恢复必须**无论成败都执行**，否则一次断言失败就会污染同一进程里后续的测试。
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
    # 自己这个目录也尽力删一次（多数情况下删不掉：应用还持有 sqlite 句柄）；
    # 删不掉不要紧，下一次运行开头的 cleanup_stale_folders 会收拾它。
    shutil.rmtree(folder, ignore_errors=True)
    assert result.status_code == 200, result.text
    data = result.json()
    coverage = data["metadata"]["coverage"]
    findings_json = json.dumps(coverage.get("findings") or [], ensure_ascii=False)
    # 判"还活到最终材料里"要看**模型实际收到的那份文字**（`final_context`），
    # 而不是 `final_context + findings`：后者里有各批次原始摘引，用它会让"压缩丢句"永远测不出来。
    # 只有真的没有 final_context（例如单遍直读）时才退回到 findings 文本。
    final_text = coverage.get("final_context") or findings_json
    answer = data["answer"]
    # 分环节判定：读到了 / 压缩后还在 / 最终材料里还在。三个环节分开报，
    # 否则"哪一步丢的"只能靠猜（这正是本基准要避免的）。
    # 判据用"去掉空白、折平引号后包含"：与链路里逐字核验同一口径（排版换行与弯引号不该算不同）。
    def contains(haystack, needle):
        return normalize_text(needle) in normalize_text(haystack)
    rows = []
    # "读到哪一批"按**实际批次数**折叠：重试会把同一批记多次（那正是重试），
    # 不折叠的话读到的陷阱会看起来比实际多。
    batches = coverage.get("batches") or 1
    read_batches = [batch for batch in batches_read[:batches]]
    if not read_batches:
        read_batches = [[name for name, sentence in TRAPS.items() if contains(findings_json, sentence)]]
    seen_in_batches = {name for batch in read_batches for name in batch}
    if os.environ.get("TRAP_BENCH_DEBUG"):
        print(f"[debug] findings_bytes={len(findings_json)} read_batches={read_batches} "
              f"seen={sorted(seen_in_batches)}", file=sys.stderr)
    for name, sentence in TRAPS.items():
        rows.append({
            "trap": name,
            "page": (traps_page.get(name) or [None])[0],
            "in_batch": name in seen_in_batches,
            "in_findings": contains(findings_json, sentence),
            "in_final": contains(final_text, sentence),
            "in_answer": contains(answer, sentence) or contains(answer, sentence[:40]),
        })
    return {
        "label": label, "pages": len(pages), "batches": coverage.get("batches"),
        "multi_pass": coverage.get("multi_pass"), "calls": len(calls),
        "depth": sequential_depth(calls), "seconds": round(elapsed, 2),
        "traps": rows, "coverage_ledger": coverage.get("coverage"),
        # 证据银行的账目要能单独看到：`kept/available/dropped` 回答的是
        # "核验过的摘引有没有完整进入最终材料"，与陷阱句是否出现互相印证。
        "evidence_bank": coverage.get("evidence_bank"),
        "final_context_head": (coverage.get("final_context") or "")[:200],
        "full_extracted_text": coverage.get("full_extracted_text"),
    }


def sequential_depth(calls):
    """串行深度：按阶段名折叠成"必须顺序执行的段数"（同阶段相邻的调用算 1 层）。

    只用**阶段序列**，所以重试（同阶段连着调多次）不会虚增深度——重试确实增加耗时，
    但它不是"串行的阶段数"，两件事要分开看。
    """
    depth = 0
    previous = None
    for name in calls:
        if name != previous:
            depth += 1
        previous = name
    return depth


def call_summary(calls):
    """每次调用的阶段计数（重试单独看得见）。"""
    counts = {}
    for name in calls:
        counts[name] = counts.get(name, 0) + 1
    return counts


def mark_of(trap):
    """三种结局各自的标记。用 ASCII 记号，避免在 GBK 控制台上打印直接崩掉。"""
    if trap["in_final"]:
        return "[OK]"
    return "[LOST]" if trap["in_batch"] else "[MISS]"


def main():
    cases = [
        dict(padding_pages=0, boundary_gap=0, label="small"),
        dict(padding_pages=60, boundary_gap=6, label="large"),
    ]
    results = [run(**case) for case in cases]
    total = len(TRAPS)
    print(f"{'用例':<8}{'页数':>5}{'批数':>5}{'调用':>5}{'深度':>5}{'耗时s':>8}"
          f"{'读到时':>8}{'压缩后':>8}{'材料中':>8}{'答案中':>8}")
    for row in results:
        read = sum(1 for trap in row["traps"] if trap["in_batch"])
        kept = sum(1 for trap in row["traps"] if trap["in_final"])
        said = sum(1 for trap in row["traps"] if trap["in_answer"])
        print(f"{row['label']:<8}{row['pages']:>5}{row['batches']:>5}{row['calls']:>5}"
              f"{row['depth']:>5}{row['seconds']:>8}"
              f"{read:>6}/{total}{kept:>6}/{total}{kept:>6}/{total}{said:>6}/{total}")
        for trap in row["traps"]:
            print(f"    {mark_of(trap):<7}{trap['trap']:<20} 第{trap['page']}页")
    print()
    print("列含义：读到时 = 该句出现在阅读记录里；压缩后/材料中 = 它还活到最终综合材料里。")
    print("[LOST] 表示读到了却在压缩阶段丢掉——这正是 Evidence Retention Recall 要测的东西。")
    print("[MISS] 表示根本没进任何批次（分批或范围问题）。")
    print("本基准用假模型（只照抄它在材料里看见的陷阱句），因此丢句只能来自链路，不会与模型能力混淆。")
    return results


if __name__ == "__main__":
    main()
