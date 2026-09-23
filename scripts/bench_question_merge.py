"""量化"提问后理解问题有多慢"：合并前后各要几次模型调用、是否还扫全篇章节大纲。

背景（用户上报）："提问后理解问题、阅读原文速度非常慢"。查明原因之一是**同一句问题被送进
模型两遍**——先问"要不要读文献"（`question_route`），再问"读多少"（`reading.plan_question`），
第二遍问完才开始读原文。现在两个判断合成一次调用。

本脚本用**假模型**（不联网、不调用任何付费服务）跑一轮真实的 `/api/chat`，只统计可核对的量：

- 模型调用次数与各次调用的阶段名；
- `reading_adapter.document_structure` 的 `lru_cache` 是否被填充过——旧路径会为了第二次
  范围判断去扫全篇找标题（`planning_context`），长文献上这是实打实的等待；合并之后连它都不必扫。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe -m scripts.bench_question_merge

结果是对"调用次数与是否扫大纲"的度量，不是对某个平台往返延迟的复现。
"""
import json
import os
import re
import sys

PAGES = int(os.environ.get("BENCH_PAGES", "12"))
QUESTION = os.environ.get("BENCH_QUESTION", "这篇文献的结论是否成立")


def respond(body):
    """假模型：按 system 提示词分派，摘引一律取自材料，保证逐字可核验。"""
    system = " ".join(m["content"] for m in body["messages"] if m["role"] == "system")
    material = body["messages"][-1]["content"]
    if isinstance(material, list):
        material = " ".join(part.get("text", "") for part in material if isinstance(part, dict))
    if "QUESTION_ROUTE" in system:
        # 合并口径的回答：路由字段 + 阅读范围字段（真实模型就是这么回的）。
        return json.dumps({"document": True, "search": False, "reason": "论文结论", "reply": "",
                           "reading": "document", "confidence": 0.9, "pages": [], "queries": []})
    if "READING_PLAN" in system:
        return json.dumps({"breadth": "document", "confidence": 0.9, "reason": "结构问题"})
    found = re.search(r"\[(\d+)\][^\n]*\n(.{20,90})", material, re.S)
    source = int(found.group(1)) if found else 1
    excerpt = found.group(2).split("\n")[0][:60] if found else "evidence"
    if "READING_AUDIT" in system:
        return json.dumps({"claims": [{"claim": 1, "verdict": "supported", "reason": "有原文支持",
                                       "evidence": [{"source": source, "quote": excerpt}]}]})
    return f"本文围绕一个核心论断展开 [1]。"


def main():
    folder = os.path.join(os.getcwd(), f".test-bench-merge-{os.getpid()}")
    os.makedirs(folder, mode=0o777, exist_ok=True)
    os.environ["READER_DATA_DIR"] = folder
    os.environ["READER_API_BASE"] = "http://localhost:9999/v1"
    sys.path.insert(0, os.getcwd())

    import httpx
    from fastapi.testclient import TestClient
    from tests.test_app import pdf_bytes

    pages = ["\n".join(f"Page {n} line {i}: evidence and qualifications for the claim."
                       for i in range(30)) for n in range(1, PAGES + 1)]
    calls = []

    class Fake:
        def __init__(self, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def close(self): pass
        def post(self, url, **kw):
            payload = kw["json"]
            text = respond(payload)
            calls.append(" ".join(m["content"] for m in payload["messages"] if m["role"] == "system"))
            raw = json.dumps(payload["messages"], ensure_ascii=False)

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
    from app import reading_adapter
    # 只数"为了第二次范围判断才做的准备"：planning_context 是它唯一的消费者（合并后不再需要）。
    context_calls = []
    original_context = reading_adapter.planning_context
    def counting_context(host, body, history):
        context_calls.append(1)
        return original_context(host, body, history)
    reading_adapter.planning_context = counting_context
    import app.plugins.chat_feature as chat_feature
    chat_feature.planning_context = counting_context

    with TestClient(main.app) as client:
        created = client.post("/api/documents", files={"file": ("bench.pdf", pdf_bytes(pages), "application/pdf")})
        assert created.status_code == 200, created.text
        result = client.post("/api/chat", json={"document_id": created.json()["id"], "query": QUESTION})
        assert result.status_code == 200, result.text
        data = result.json()

    def stage(system):
        for tag, label in (("QUESTION_ROUTE", "理解问题（含阅读范围）"),
                           ("READING_BATCH", "分批阅读原文"),
                           ("READING_REDUCE", "合并阅读记录"),
                           ("READING_AUDIT", "核对论断与原文"),
                           ("READING_REREAD", "检查遗漏并回读"),
                           ("READING_FOCUS", "压缩资料腾空间"),
                           ("READING_PLAN", "判断阅读范围（旧路径的第二次调用）")):
            if tag in system: return label
        return "形成文献分析"

    outline_built = reading_adapter.document_structure.cache_info().currsize
    print(f"文献：{len(pages)} 页 ／ 问题：{QUESTION}")
    print(f"模型调用：{len(calls)} 次")
    for index, system in enumerate(calls, 1):
        print(f"  {index}. {stage(system)}")
    print(f"范围判断的单独调用：{sum(1 for s in calls if s.lstrip().startswith('READING_PLAN'))} 次"
          "（旧路径固定 1 次；合并后为 0）")
    print(f"为第二次判断扫全篇章节大纲：{len(context_calls)} 次（旧路径固定 1 次；合并后为 0）")
    print(f"（本轮读整篇时章节树本来就要构建，因此大纲缓存是否非空不能用来判断这条："
          f"structures={outline_built}）")
    print(f"覆盖记录：planner={data['metadata']['coverage'].get('planner')} "
          f"strategy={data['metadata']['coverage'].get('strategy')} "
          f"复核={data['metadata']['coverage'].get('support_review',{}).get('status')}")


if __name__ == "__main__":
    main()
