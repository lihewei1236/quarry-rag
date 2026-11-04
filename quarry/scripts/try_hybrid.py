"""试跑混合检索，顺带看清两路各自的贡献。"""
import asyncio
import sys

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.retrieval.pg.searcher import hybrid_search


def _f(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


async def main(query: str, slug: str = "travel-guide") -> None:
    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                slug)
        if coll_id is None:
            print(f"集合不存在 {slug}")
            return

        hits = await hybrid_search(
            collection_id=coll_id, query=query, caller_labels=["public"])

        print(f"\n查询  {query}")
        print("=" * 64)
        for i, h in enumerate(hits, 1):
            d = h.dense_rank if h.dense_rank is not None else "-"
            s = h.sparse_rank if h.sparse_rank is not None else "-"
            print(f"\n[{i}] 融合 {h.score:.4f}  余弦 {_f(h.dense_score)}  "
                  f"dense 第 {d} 名，sparse 第 {s} 名")
            print(f"    {' > '.join(h.heading_path)}")
            print(f"    {h.content[:60]}...")
    finally:
        await close_pool()


if __name__ == "__main__":
    q = sys.argv[1] if len(sys.argv) > 1 else "兵马俑的门票多少钱"
    slug = sys.argv[2] if len(sys.argv) > 2 else "travel-guide"
    asyncio.run(main(q, slug))