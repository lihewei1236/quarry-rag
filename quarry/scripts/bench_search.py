"""压测混合检索，看并发上升时 QPS 和延迟怎么走。

默认缓存查询向量：每个查询只真实调用一次 embedding，
压测期间不再请求向量化接口，测到的是 jieba + PG 检索的性能。
加 --no-cache 可测端到端耗时（注意接口限流，并发别太高）。
"""
import argparse
import asyncio
import math
import statistics
import sys
import time

from openai.resources.embeddings import AsyncEmbeddings

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.retrieval.pg.searcher import hybrid_search

QUERIES = [
    "兵马俑的门票多少钱", "从机场到市区怎么走", "有什么本地小吃",
    "开放时间是几点", "需要提前预约吗", "住宿大概什么价位",
]

# ---------------- embedding 缓存 ----------------
# 只在压测脚本里临时替换 openai 客户端的 create 方法，不影响业务代码
_orig_create = AsyncEmbeddings.create
_cache: dict[str, object] = {}
_stats = {"hit": 0, "miss": 0}


async def _cached_create(self, *args, **kwargs):
    key = repr((args, sorted(kwargs.items())))
    if key in _cache:
        _stats["hit"] += 1
        return _cache[key]
    _stats["miss"] += 1
    resp = await _orig_create(self, *args, **kwargs)
    _cache[key] = resp
    return resp


def enable_embed_cache():
    AsyncEmbeddings.create = _cached_create


# ---------------- 压测 ----------------
def pct(sorted_vals: list[float], p: float) -> float:
    idx = max(0, math.ceil(len(sorted_vals) * p) - 1)
    return sorted_vals[idx]


async def bench(coll_id, concurrency: int, total: int):
    sem = asyncio.Semaphore(concurrency)
    latencies: list[float] = []
    errors = 0

    async def one(i: int):
        nonlocal errors
        async with sem:
            t0 = time.perf_counter()
            try:
                await hybrid_search(collection_id=coll_id,
                                    query=QUERIES[i % len(QUERIES)],
                                    caller_labels=["public"], top_k=10)
            except Exception as e:
                errors += 1
                if errors == 1:
                    print(f"  首个错误：{type(e).__name__}: {str(e)[:120]}",
                          file=sys.stderr)
                return
            latencies.append((time.perf_counter() - t0) * 1000)

    start = time.perf_counter()
    await asyncio.gather(*[one(i) for i in range(total)])
    elapsed = time.perf_counter() - start

    ok = len(latencies)
    if not ok:
        print(f"并发 {concurrency:<4} 全部失败（{errors} 个）")
        return
    latencies.sort()
    print(f"并发 {concurrency:<4} 成功 {ok:<5} 失败 {errors:<4} 耗时 {elapsed:5.2f}s  "
          f"QPS {ok / elapsed:7.1f}  p50 {statistics.median(latencies):7.1f}ms  "
          f"p95 {pct(latencies, 0.95):7.1f}ms  p99 {pct(latencies, 0.99):7.1f}ms")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-cache", action="store_true", help="不缓存 embedding，测端到端")
    parser.add_argument("--total", type=int, default=300, help="每档请求数")
    parser.add_argument("--levels", default="1,5,10,20,50", help="并发档位，逗号分隔")
    args = parser.parse_args()
    levels = [int(x) for x in args.levels.split(",")]

    if not args.no_cache:
        enable_embed_cache()

    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = 'travel-guide' AND status = 'active'")
        if coll_id is None:
            print("找不到 slug = 'travel-guide' 的活跃集合", file=sys.stderr)
            return

        # 预热：填满缓存，同时让连接池、jieba 词典、PG 缓存都热起来
        for q in QUERIES:
            await hybrid_search(collection_id=coll_id, query=q,
                                caller_labels=["public"], top_k=10)
        mode = "端到端（含 embedding）" if args.no_cache else "已缓存 embedding（只测检索）"
        print(f"模式：{mode}，预热完成\n")

        for c in levels:
            await bench(coll_id, concurrency=c, total=args.total)

        if not args.no_cache:
            print(f"\nembedding 真实调用 {_stats['miss']} 次，缓存命中 {_stats['hit']} 次")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())