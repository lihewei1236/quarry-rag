"""拒答阈值标定：一组有答案的查询，一组肯定没有答案的，看"召回里最高的余弦分"分不分得开。

用法：
    uv run python -m quarry.scripts.calibrate_abstain            # 只看
    uv run python -m quarry.scripts.calibrate_abstain --write    # 看完写进数据库
"""
from __future__ import annotations

import argparse
import asyncio

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.retrieval.pg.searcher import hybrid_search

# 按你自己的语料改。第二组的要求是：确信语料里没有答案
HAS_ANSWER = [
    "兵马俑的门票多少钱",
    "从机场到市区怎么走",
    "有什么本地小吃",
    "景区几点开门",
    "推荐住在哪个区域",
    "当地气候怎么样",
]
NO_ANSWER = [
    "量子色动力学的渐近自由",
    "如何配置 Kubernetes 的 Ingress",
    "房贷利率怎么换算",
    "小狗拉肚子吃什么药",
    "asdkjfh qwoieur",
]


async def probe(coll_id, query: str) -> tuple[float | None, float | None]:
    """返回 (第 1 名的融合分, 召回结果里最高的余弦分)。"""
    hits = await hybrid_search(collection_id=coll_id, query=query,
                               caller_labels=["public"], top_k=30)
    if not hits:
        return None, None
    dense = [h.dense_score for h in hits if h.dense_score is not None]
    return hits[0].score, (max(dense) if dense else None)


def _f(x: float | None, digits: int) -> str:
    return "  -   " if x is None else f"{x:.{digits}f}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--write", action="store_true",
                    help="把建议阈值写进 collections.abstain_config")
    args = ap.parse_args()

    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
        if coll_id is None:
            print(f"集合不存在：{args.collection}")
            return

        print("分组      融合分   余弦分   查询")
        pos: list[float] = []
        neg: list[float] = []
        for group, queries, bucket in (("有答案", HAS_ANSWER, pos),
                                       ("没答案", NO_ANSWER, neg)):
            for q in queries:
                fused, dense = await probe(coll_id, q)
                print(f"{group}    {_f(fused, 4)}   {_f(dense, 3)}   {q}")
                if dense is not None:
                    bucket.append(dense)

        if not pos or not neg:
            print("\n有一组拿不到分数：检查集合里有没有数据、EMBED_BACKEND 是不是 api")
            return

        lo, hi = min(pos), max(neg)
        threshold = round((lo + hi) / 2, 2)
        print(f"\n有答案最低 {lo:.3f}，没答案最高 {hi:.3f}，建议阈值 {threshold}")
        if lo <= hi:
            print("⚠️  两组有重叠：先检查重叠的那几条查询选得是否合适，"
                  "第七章会用标注集重新标定")

        if args.write:
            async with acquire() as conn:
                await conn.execute(
                    "UPDATE collections SET abstain_config = $1 WHERE id = $2",
                    {"enabled": True, "min_dense_score": threshold}, coll_id)
            print(f"已写入 {args.collection} 的 abstain_config")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())