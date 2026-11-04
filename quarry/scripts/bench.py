"""压测：并发跑标注集里的问题，统计延迟分位数和各阶段耗时。

用法：uv run python -m quarry.scripts.bench [--concurrency 4] [--requests 60] [--no-rerank]
并发从 2 起步慢慢加，别把厂商接口的限流打爆。
"""
from __future__ import annotations

import argparse
import asyncio
import time

from quarry.eval.metrics import pad, percentile
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.settings import get_settings
from quarry.pipeline import search_evidence

STAGES = ["lookup_ms", "recall_ms", "rerank_ms", "expand_ms", "facts_ms", "assemble_ms"]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--requests", type=int, default=60)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--no-facts", action="store_true")
    args = ap.parse_args()

    s = get_settings()
    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
            queries = [r["query"] for r in await conn.fetch(
                "SELECT query FROM eval_queries WHERE collection_id = $1"
                " AND kind = 'answerable'", coll_id)]
        if not queries:
            print("标注集里没有有答案的问题，先跑 7.2 的导入脚本")
            return

        totals: list[float] = []
        stages: dict[str, list[float]] = {k: [] for k in STAGES}
        failures = 0
        counter = 0
        lock = asyncio.Lock()

        async def worker() -> None:
            nonlocal counter, failures
            while True:
                async with lock:
                    if counter >= args.requests:
                        return
                    query = queries[counter % len(queries)]
                    counter += 1
                started = time.perf_counter()
                try:
                    r = await search_evidence(args.collection, query,
                                              caller_labels=["public"],
                                              top_k=s.eval_top_k,
                                              use_rerank=not args.no_rerank,
                                              use_facts=not args.no_facts)
                except Exception:
                    failures += 1
                    continue
                totals.append((time.perf_counter() - started) * 1000)
                for k in STAGES:
                    if k in r.timings:
                        stages[k].append(r.timings[k])

        wall = time.perf_counter()
        await asyncio.gather(*(worker() for _ in range(args.concurrency)))
        wall = time.perf_counter() - wall
    finally:
        await close_pool()

    print(f"\n并发 {args.concurrency}，请求 {len(totals)} 成功 / {failures} 失败，"
          f"耗时 {wall:.1f} 秒，吞吐 {len(totals) / max(wall, 0.001):.1f} 次/秒")
    print(f"重排 {'关' if args.no_rerank else '开'}，"
          f"事实层 {'关' if args.no_facts else '开'}\n")
    print("  " + pad("阶段", 16) + f"{'P50':>9}{'P95':>9}{'P99':>9}")
    for k in STAGES + ["合计"]:
        values = totals if k == "合计" else stages[k]
        if values:
            print("  " + pad(k, 16) + f"{percentile(values, 50):>9}"
                                      f"{percentile(values, 95):>9}"
                                      f"{percentile(values, 99):>9}")
    if percentile(totals, 95) > 800:
        print("\n⚠️ P95 超过 800 毫秒的预算，看看哪一段最贵（一般是重排）")


if __name__ == "__main__":
    asyncio.run(main())