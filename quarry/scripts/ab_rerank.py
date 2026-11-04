"""重排开 / 关对照：同一批候选，重排后的第 1 名原来排第几、多花了多少时间。"""
from __future__ import annotations

import asyncio
import statistics
import time

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.settings import get_settings
from quarry.rerank import get_reranker
from quarry.retrieval.pg.searcher import hybrid_search

QUERIES = [
    "兵马俑的门票多少钱",
    "从机场到市区怎么走",
    "有什么本地小吃值得尝",
    "景区几点开门",
    "需要提前预约吗",
    "住在哪个区域比较方便",
    "冬天去合适吗",
    "有没有适合带孩子去的地方",
    "当地交通卡怎么买",
    "去下一个城市坐什么车",
]


async def main() -> None:
    s = get_settings()
    reranker = get_reranker()
    if reranker.name == "noop":
        print("RERANK_BACKEND=noop，重排前后会一模一样。先按 5.2.3 改成 api。")
        return

    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = 'travel-guide' AND status = 'active'")
        if coll_id is None:
            print("集合 travel-guide 不存在，先跑第三章的 ingest_corpus")
            return

        print(f"候选数 {s.rerank_candidates}\n")
        print("原排名  第1名  重排耗时   查询")
        positions: list[int] = []
        costs: list[float] = []

        for q in QUERIES:
            hits = await hybrid_search(collection_id=coll_id, query=q,
                                       caller_labels=["public"],
                                       top_k=s.rerank_candidates)
            if not hits:
                print(f"   -     -          -   {q}（没有召回）")
                continue
            before = [h.chunk_id for h in hits]

            t0 = time.perf_counter()
            ranked = await reranker.rerank(q, hits, top_k=len(hits))
            cost = (time.perf_counter() - t0) * 1000

            # 闸门：没有真正重排过，这张表就没有意义
            if ranked[0].rerank_score is None:
                print(f"\n❌ 「{q}」没有真正重排，对照结果作废。先跑 verify_rerank 排查。")
                return

            pos = before.index(ranked[0].chunk_id) + 1
            positions.append(pos)
            costs.append(cost)
            print(f"{pos:>4}    {'换了' if pos != 1 else '没换'}   {cost:6.0f} ms   {q}")

        if positions:
            changed = sum(1 for p in positions if p != 1)
            print("\n" + "=" * 48)
            print(f"第 1 名被换掉    {changed}/{len(positions)}")
            print(f"重排耗时中位数  {statistics.median(costs):.0f} ms")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())