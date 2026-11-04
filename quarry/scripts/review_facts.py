"""抽样核对：随机取 N 条提及，连同前后文一起打印，人工判断抽得对不对。

用法：uv run python -m quarry.scripts.review_facts [--n 20] [--collection travel-guide]
"""
from __future__ import annotations

import argparse
import asyncio

from quarry.facts.contract import ATTRIBUTES
from quarry.infra.db import acquire, close_pool, init_pool

_SQL = """
SELECT m.entity_name, m.attribute, m.qualifier, m.value, m.quote,
       m.span_start, m.span_end, c.content
FROM fact_mentions m
JOIN chunks c ON c.collection_id = m.collection_id AND c.chunk_id = m.chunk_id
JOIN collections k ON k.id = m.collection_id
WHERE k.slug = $1
ORDER BY random()
LIMIT $2
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()

    await init_pool()
    try:
        async with acquire() as conn:
            rows = await conn.fetch(_SQL, args.collection, args.n)
    finally:
        await close_pool()

    if not rows:
        print("fact_mentions 里没有数据，先跑 extract_facts")
        return
    for i, r in enumerate(rows, 1):
        text, s, e = r["content"], r["span_start"], r["span_end"]
        # 位置是代码找的，这里再核对一遍：对不上说明写库时出了错
        flag = "" if text[s:e] == r["quote"] else "  ❌ 偏移和原文对不上"
        before = text[max(0, s - 25):s].replace("\n", " ")
        after = text[e:e + 25].replace("\n", " ")
        print(f"[{i:>2}] {r['entity_name']} · {ATTRIBUTES[r['attribute']]} · "
              f"{r['qualifier']} = {r['value']}{flag}")
        print(f"     …{before}【{text[s:e]}】{after}…\n")
    print("逐条判断：实体、属性、季节、值四项都对才算对。准确率 = 对的条数 / 总条数。")


if __name__ == "__main__":
    asyncio.run(main())