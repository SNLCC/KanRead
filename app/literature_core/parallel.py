"""有界并发的小工具：**只做"同层独立任务的并行"，不改变任何语义**。

为什么要单独一个文件：并发必须能被单独测试，也必须能被单独关掉。这里只有标准库
（``concurrent.futures`` + ``contextvars``），不引入任何新依赖，也不包含任何第三方代码。

三条硬约束（都是本项目的口径，不是通用最佳实践）：

1. **结果顺序必须等于输入顺序**。阅读批次按原文顺序分，若让"谁先回来谁先进结果"，
   同一问题会因为完成顺序不同而得到不同答案——那是不可接受的波动。
   因此这里返回**与输入等长的列表**，失败的位置是 None，绝不重排。
2. **取消必须能传播进子线程**。``cancellation.check()`` 读的是 contextvar，子线程默认拿不到，
   所以每个任务都在 ``copy_context()`` 的副本里跑（取消、进度上报因此在子线程里照常生效）。
3. **并发必须有上限，并且能退让**。本机模型（Ollama 之类）并发跑多个请求只会互相争抢 CPU；
   云端平台则有速率限制。上限由调用方按 profile 给，默认取保守值。
"""
import contextvars
from concurrent.futures import ThreadPoolExecutor

# 保守默认值：宁可慢一点，也不要在本机模型上并发争抢、或在云端触发 429
# （429 会让整轮更慢，也让用户看到失败）。真正的取值要靠 benchmark 决定，不在这里写死。
#
# 实测（`scripts/bench_reading_concurrency.py`，假模型每次调用固定 0.3s、23 批）：
#
#     workers  耗时    加速
#        1     8.72s   1.00×
#        2     4.82s   1.81×
#        4     2.71s   3.21×
#        8     2.71s   3.22×   ← 与 4 同速：**收益在 4 附近就吃完了**
#
# 因此默认取 **4**（第 89 轮从 3 提到 4，用户也要求默认 4）：4 就是收益的拐点，
# 再往上（8）与 4 同速，只会更容易触怒平台限流。
# 上限放到 8 是留给"平台允许且批次很多"的情形——加档不再有收益时用户会自己看报告发现。
DEFAULT_WORKERS = 4
MAX_WORKERS = 8


def governor(profile):
    """按模型服务给一个并发上限。

    **用户在设置里明确填了就用它**（`max_parallel`）：并发上限应该由实测决定，
    而不是由一个写死的常数决定——不同平台的限流差别极大，本机模型则受 CPU/GPU 制约。
    设置里留空（0）时退回保守默认：

    - 本机/局域网（localhost、*.local）：1——瓶颈是 CPU/GPU，并发只会互相拖慢；
    - 远程服务：4——实测 4 就是收益拐点（3.21×），8 与 4 同速。

    实测（`scripts/bench_reading_concurrency.py`，假模型定延时）23 批的用例：
    workers=1 8.72s、2 4.82s（1.81×）、4 2.71s（3.21×）、8 2.71s（3.22×，与 4 同速），
    而调用次数、批数与证据条数在四种取值下**完全相同**——并发只改变耗时。
    """
    from urllib.parse import urlsplit
    try:
        chosen=int(profile.get('max_parallel') or 0)
    except (TypeError,ValueError):
        chosen=0
    if chosen>0:
        return max(1,min(MAX_WORKERS,chosen))
    host = (urlsplit(profile.get("base_url", "")).hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "::1") or host.endswith(".local"):
        return 1
    return DEFAULT_WORKERS


def is_cancellation(exc):
    """这个异常是不是"用户点了停止"（HTTP 499）？**要顺着异常链找**。

    有些包装会用 ``raise X from None`` 把真正的原因藏进 ``__context__``：只看最外层，
    就会把"用户点了停止"误判成"某一批失败"，于是剩下的批次继续跑——那是本项目不允许的。
    """
    seen = set()
    while exc is not None and id(exc) not in seen:
        if getattr(exc, "status_code", None) == 499:
            return True
        seen.add(id(exc))
        exc = exc.__cause__ or exc.__context__
    return False


def parallel_map(items, worker, limit=DEFAULT_WORKERS, check=None, fatal=is_cancellation):
    """按 ``limit`` 并发执行 ``worker(item)``，返回**与 items 等长**的结果列表。

    - 顺序 = 输入顺序（索引回填），因此调用方永远可以按下标取结果；
    - ``worker`` 抛异常 → 该位置为 ``None``（其它位置照常返回），不把整轮炸掉；
      调用方据此把那一项当作"需要重试/失败"处理，与串行时的语义一致；
    - 但 ``fatal(exc)`` 为真的异常**立即向上抛**（默认就认取消）："整轮必须停"的错误不能被
      当成"某一项失败"吞掉——那会让用户点了停止却继续跑完剩下的批次；
    - ``limit <= 1`` 或只有一项时**直接串行执行**：不建线程池，行为与改造前逐字节一致
      （这样"关掉并发"是一条真正可用的退路，而不是另一条代码路径）；
    - ``check`` 在每个任务开始前调用（取消检查因此能传播到子线程）。
    """
    items = list(items)
    if not items:
        return []
    is_fatal = fatal or (lambda exc: False)

    def run(item):
        if check:
            check()
        try:
            return worker(item)
        except Exception as exc:
            if is_fatal(exc):
                raise
            return None

    if limit <= 1 or len(items) == 1:
        # 串行时不吞异常：调用方（例如读取批次）本来就要看见真实的失败原因。
        if check:
            check()
        return [worker(item) for item in items]
    workers = max(1, min(int(limit), MAX_WORKERS, len(items)))
    results = [None] * len(items)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {}
        for index, item in enumerate(items):
            # 每个任务复制一份当前上下文：cancellation.check() 与进度上报在子线程里才有效。
            context = contextvars.copy_context()
            futures[pool.submit(context.run, run, item)] = index
        for future in futures:
            index = futures[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                if is_fatal(exc):
                    raise
                results[index] = None
    return results
