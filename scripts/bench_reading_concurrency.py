"""量化"分批阅读"阶段的墙钟时间：workers=1 与 workers=2 对比（本机合成原文 + 假模型，不联网）。

为什么要单独量这一段：一轮问答里最长的串行段就是逐批读原文（远程模型上每批一次请求）。
第 65 轮把同层独立的 reduce / 核对改成并发，本轮把批次阅读也改成"按批次顺序滑动窗口、
窗口内并发"。这份脚本用**固定延时的假模型**把链路自身的并发度测出来：

- 假模型每次调用固定延时（默认 0.3 秒），因此耗时差只来自链路的串行/并发结构，
  与模型速度、网络、平台限流无关；
- 同时报出调用次数与证据条数：并发**不许**改变它们（改变了就是错的，不是"更快"）。

用法（仓库根目录）::

    .venv\\Scripts\\python.exe -m scripts.bench_reading_concurrency

可用环境变量：BENCH_PAGES（默认 24）、BENCH_PER_BATCH（每批几页，默认 2）、BENCH_LATENCY（每次调用延时秒数）。
"""
import json
import os
import re
import sys
import time

PAGES = int(os.environ.get("BENCH_PAGES", "24"))
PER_BATCH = int(os.environ.get("BENCH_PER_BATCH", "2"))
LATENCY = float(os.environ.get("BENCH_LATENCY", "0.3"))
# 假模型每批写多长（重复几遍"论断与限定。"）：用来量"收紧 output 上限"的效果。
ANALYSIS_FACTOR = int(os.environ.get("BENCH_ANALYSIS", "10"))
# 每页原文有多长。**它不是装饰**：预算由原文长度算出，而每批的分析记录必须装得进预算，
# 所以"旧写法那种长分析"只有在原文足够长时才跑得起来（把原文改短会让长分析直接报
# "单批阅读记录过长"——那是真实的预算约束，不是脚本的毛病，第 87 轮实测时踩到过）。
PAGE_PAD = int(os.environ.get("BENCH_PAGE_PAD", "6"))


def source_rows():
    """每页一段原文：内容不重要，长度决定分成几批。"""
    return [{'id': f'p{page}', 'document_id': 'bench', 'name': 'bench.pdf', 'page': page,
             'text': f'Page {page}. The study reports that the effect increases with exposure time, '
                     f'conditional on controlling for sample heterogeneity and measurement drift. '
                     + 'Additional qualifying discussion follows. ' * PAGE_PAD} for page in range(1, PAGES + 1)]


def run(workers):
    from app.literature_core import reading
    rows = source_rows()
    # 预算刚好装下 PER_BATCH 页，因此批次数 = 页数 / 每批页数（不由模型或调度决定）。
    budget = (reading.cost(reading.source_text(rows[0], 1)) + 2) * PER_BATCH + 5
    calls = []
    produced = []

    def ask(instruction, material):
        calls.append(1)
        time.sleep(LATENCY)
        if 'READING_BATCH' in instruction:
            number = int(re.search(r'\[(\d+)\] [^\n]+\n', material).group(1))
            # 假模型**照指令的长度要求**产出一段分析：这样"指令收紧后产出是否真的变短"
            # 才是可量的（`BENCH_ANALYSIS` 控制它写多长，默认取当前上限的一半）。
            text = json.dumps({'analysis': '本批记录：' + ('论断与限定。' * ANALYSIS_FACTOR),
                               'evidence': [{'source': number, 'quote': f'Page {number}. The study reports that the effect increases'}]})
        else:
            text = json.dumps({'analysis': '合并后的分析。'})
        produced.append(text)
        return text

    started = time.perf_counter()
    context, report = reading.read_in_batches(rows, budget, ask, lambda: None, workers=workers)
    seconds = time.perf_counter() - started
    bank = reading.split_context(context)[1]
    return {'workers': workers, 'seconds': seconds, 'calls': len(calls), 'batches': report['batches'],
            'kept': len(bank), 'analysis_chars': sum(len(item) for item in produced)}


def main():
    print(f"页数 {PAGES} · 每批 {PER_BATCH} 页 · 假模型每次调用延时 {LATENCY:.2f}s（不联网、不调用任何付费服务）")
    print('（预算是故意压小的：批次数多，才量得出"每批一次请求"的串行代价；'
          '因此带进最终材料的证据条数受 fit_bank 的预算上限限制）')
    # workers 取值由 BENCH_WORKERS 给（逗号分隔）。默认 1/2/4：**上限取值要靠数字定**，
    # 不是一个写死的常数——本机模型并发只会争抢 CPU，云端则受平台限流，两者的最优值不同。
    ladder = [int(item) for item in os.environ.get("BENCH_WORKERS", "1,2,3,4").split(",") if item.strip()]
    results = [run(workers) for workers in ladder]
    print(f"{'workers':>8}{'批数':>6}{'调用':>6}{'证据':>6}{'分析字符':>10}{'耗时s':>9}{'加速':>8}")
    base = results[0]['seconds']
    for row in results:
        speed = f"{base / row['seconds']:.2f}×" if row['seconds'] > 0 else '-'
        print(f"{row['workers']:>8}{row['batches']:>6}{row['calls']:>6}{row['kept']:>6}"
              f"{row['analysis_chars']:>10}{row['seconds']:>9.2f}{speed:>8}")
    # 每一个取值都必须与串行给出**完全相同**的调用次数、批数与证据条数：
    # 并发只许改变耗时，改变别的就是错的（不是"更快"）。
    serial = results[0]
    for row in results[1:]:
        assert serial['calls'] == row['calls'], f"workers={row['workers']} 改变了调用次数"
        assert serial['kept'] == row['kept'], f"workers={row['workers']} 改变了证据条数"
        assert serial['batches'] == row['batches'], f"workers={row['workers']} 改变了分批方式"
        assert serial['analysis_chars'] == row['analysis_chars'], f"workers={row['workers']} 改变了产出长度"
    print("所有取值下调用次数、批数、证据条数与产出长度完全一致（改变了就是错的，不是更快）。")
    print(f"理论上限：加速不超过 workers（同一时间最多 workers 批在飞）；"
          f"实测是否收敛要看批次总数（本次 {serial['batches']} 批）。")
    print(f"本轮的产出规模：{serial['calls']} 次调用合计写出 {serial['analysis_chars']} 字符的分析。"
          '（BENCH_ANALYSIS 控制假模型每批写多长，可用它对比「收紧上限」前后。）')
    return results


if __name__ == "__main__":
    sys.path.insert(0, os.getcwd())
    main()
