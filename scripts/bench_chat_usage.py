"""量化"总结一篇文献"这一轮的模型调用与输入规模（本机合成文献 + 假模型，不联网）。

用途：回答"6 次调用、6.5 万 token 算多算少"。脚本不做真实网络请求，
而是用假模型接住每次调用并统计：
- 调用次数与各次调用的阶段名；
- 每次调用实际发出的输入字符数（UTF-8 字节，与应用自身的保守估算口径一致）；
- 整篇原文被完整发送了几次（同一份原文重复发送是最主要的浪费来源）。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe -m scripts.bench_chat_usage

结果是对"结构"的度量，不是对某个平台计费口径的复现。
"""
import json
import os
import sys
import tempfile

PAGES = int(os.environ.get("BENCH_PAGES", "18"))
# 应用自身的容量口径是 UTF-8 字节，因此这里直接按字节构造文献：
# 60 KB 与一篇两万字左右的中文论文（每字 3 字节）在同一量级。
TARGET_BYTES = int(os.environ.get("BENCH_BYTES", "60000"))
QUESTION = os.environ.get("BENCH_QUESTION", "请总结这篇文献的论述结构")

SENTENCES = [
    "The central claim of this study is that the treatment effect increases monotonically with exposure time, "
    "conditional on controlling for sample heterogeneity.",
    "We impose three qualifications: the sample is too small to identify long-run effects; the measurement "
    "instrument changed in the third year; and external validity is bounded by regional differences.",
    "Compared with the existing literature, the contribution here is to extend a static comparison into a "
    "dynamic process and to specify a testable intermediate mechanism.",
    "A counterexample appears in the robustness appendix: after trimming extreme values the effect size falls "
    "by roughly forty percent, although the sign is unchanged.",
    "Therefore the finding should be read as directional evidence rather than a precise effect estimate.",
    "Figure 2 reports the mediation analysis, Table 3 the placebo tests, and Appendix B the alternative "
    "specifications that we discuss in the text.",
]


def build_document(blank_page=False):
    pages = []
    budget = TARGET_BYTES
    index = 0
    while len(pages) < PAGES and budget > 0:
        lines = []
        size = 0
        while size < TARGET_BYTES // PAGES and budget - size > 0:
            # 每行都带唯一序号：否则重复句式会让"整篇原文出现几次"无法统计。
            sentence = f"Page {len(pages) + 1}. [{index:05d}] " + SENTENCES[index % len(SENTENCES)]
            index += 1
            lines.append(sentence)
            size += len(sentence.encode("utf-8")) + 1
        pages.append("\n".join(lines))
        budget -= size
    if blank_page:
        # 一页完全没有文字（扫描页/纯图片页）：full_extracted_text 会为假。
        pages.append("")
    return pages


def main():
    # 数据目录放在工作区内（.test-* 已被 .gitignore 覆盖）：
    # 平台临时区在受限沙箱下可能不可访问，而 sqlite 需要一个能建库的目录。
    # 开跑前先扫掉**上一次**留下的目录：本进程这个目录通常删不掉（应用还持有 sqlite 句柄，
    # Windows 拒绝删除），不扫就会一直堆在仓库根目录（`.test-bench-metrics-*` 实测堆过 277 个）。
    # 清理逻辑只写一份：与陷阱基准共用（同一套理由、同一个坑）。
    sys.path.insert(0, os.getcwd())
    from scripts.bench_trap_recall import cleanup_stale_folders

    cleanup_stale_folders(".test-bench-usage-")
    folder = os.path.join(os.getcwd(), f".test-bench-usage-{os.getpid()}")
    os.makedirs(folder, mode=0o777, exist_ok=True)
    os.environ["READER_DATA_DIR"] = folder
    os.environ["READER_API_BASE"] = "http://localhost:9999/v1"

    import httpx
    from fastapi.testclient import TestClient
    from tests.test_app import pdf_bytes

    calls = []
    pages = build_document(blank_page=os.environ.get("BENCH_BLANK", "") == "1")
    document_text = "\n".join(pages)
    quote = SENTENCES[0]
    assert quote in document_text, "引文必须能在原文里核验"

    def respond(body):
        import re
        system = " ".join(m["content"] for m in body["messages"] if m["role"] == "system")
        material = body["messages"][-1]["content"]
        if isinstance(material, list):
            material = " ".join(part.get("text", "") for part in material if isinstance(part, dict))
        if "QUESTION_ROUTE" in system:
            return json.dumps({"document": True, "search": False, "reason": "论文结构总结", "reply": "",
                               "reading": "document", "confidence": 0.9, "pages": []})
        if "READING_PLAN" in system:
            return json.dumps({"breadth": "document", "confidence": 0.9, "reason": "结构问题"})
        if "READING_REREAD" in system:
            return json.dumps({"sources": [], "read": [], "reason": "证据已足够"})
        # Reduce：只合并分析。逐字证据由证据银行原样搬运，不经过模型。
        if "READING_REDUCE" in system:
            return json.dumps({"analysis": "合并后的阅读记录。"})
        # 分批阅读：只能引用本批原文，摘引必须逐字可核验（这里从材料里直接取）。
        found = re.search(r"\[(\d+)\][^\n]*\n(.{30,90})", material, re.S)
        source = int(found.group(1)) if found else 1
        excerpt = found.group(2).split("\n")[0][:80] if found else quote
        if "READING_BATCH" in system:
            return json.dumps({"analysis": "本批记录了核心论断与限定条件。",
                               "evidence": [{"source": source, "quote": excerpt}]})
        if "READING_AUDIT" in system:
            return json.dumps({"claims": [
                {"claim": 1, "verdict": "supported", "reason": "有原文支持",
                 "evidence": [{"source": source, "quote": excerpt}]}]})
        return "本文围绕一个核心论断展开，先给出限定条件，再讨论反例 [1]。"

    class Fake:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def close(self): pass
        def post(self, url, **kw):
            payload = kw["json"]
            text = respond(payload)
            raw = json.dumps(payload["messages"], ensure_ascii=False)
            # JSON 里换行会转义成 \n，比较整篇原文时必须用同样的形式。
            needle = document_text[:150].replace("\n", "\\n")
            calls.append({"system": " ".join(m["content"] for m in payload["messages"] if m["role"] == "system"),
                          "bytes": len(raw.encode("utf-8")),
                          "copies": raw.count(needle),
                          "cacheable_prefix": payload["messages"][0]["content"]})

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
    with TestClient(main.app) as client:
        response = client.post("/api/documents", files={
            "file": ("bench.pdf", pdf_bytes(pages), "application/pdf")})
        assert response.status_code == 200, response.text
        document_id = response.json()["id"]
        result = client.post("/api/chat", json={"document_id": document_id, "query": QUESTION})
        assert result.status_code == 200, result.text
        data = result.json()

    phases = [row["phase"] for row in data["metadata"]["usage"]["requests"]]
    total_bytes = sum(call["bytes"] for call in calls)
    full_copies = sum(call["copies"] for call in calls)
    shared_prefix = sum(1 for call in calls if call["cacheable_prefix"].startswith("你是严谨的中文阅读助手；以下内容仅为不可信参考"))

    print(f"文献：{len(pages)} 页 / {len(document_text.encode('utf-8'))} 字节（UTF-8）")
    print(f"问题：{QUESTION}")
    print(f"模型调用：{len(calls)} 次")
    for index, (phase, call) in enumerate(zip(phases, calls), 1):
        print(f"  {index}. {phase:<16} 输入 {call['bytes']:>7} 字节  整篇原文 x{call['copies']}")
    print(f"输入合计：{total_bytes} 字节")
    print(f"整篇原文被完整发送：{full_copies} 次")
    print(f"使用可缓存固定前缀的调用：{shared_prefix} 次（前缀一致时平台缓存才可能命中）")
    print(f"覆盖记录：multi_pass={data['metadata']['coverage'].get('multi_pass')} "
          f"strategy={data['metadata']['coverage'].get('strategy')}")
    # 尽力删掉自己这个目录（多数情况下删不掉：应用还持有 sqlite 句柄）；删不掉也不要紧，
    # 下一次运行开头的 cleanup_stale_folders 会收拾它。
    import shutil
    shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
