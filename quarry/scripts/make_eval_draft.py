"""从语料里生成评测问题草稿，写成 CSV，供人工筛选。

用法：uv run python -m quarry.scripts.make_eval_draft [--n 40] [--collection travel-guide]
生成的是草稿，必须人工过一遍才能当标注集用。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
from pathlib import Path

from quarry.facts.extract import make_client            # 草稿用第六章配的 FACT_MODEL
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.logging import get_logger
from quarry.infra.settings import get_settings

log = get_logger()

SYSTEM_PROMPT = """你在为一个旅行问答系统准备评测问题。用户会给出一段攻略正文和它的标题路径。
提出一个真实用户可能会问、而且只靠这段正文就能回答的问题，只输出 JSON：
{"query": "问题"}

规则：
1. 问题里要出现具体的地名或景点名，不要用"这里""该景点"这样的指代。
2. 不要出现"这段""本文""上文"之类的词。
3. 问一个具体的信息点（价格、时间、怎么去、有什么），不要问"介绍一下"。
4. 不超过 20 个字。"""

_BLOCKS_SQL = """
SELECT c.chunk_id, c.heading_path, c.content
FROM chunks c
JOIN documents d ON d.id = c.document_id
WHERE c.collection_id = $1 AND c.level = 0 AND d.status = 'ready'
  AND char_length(c.content) >= 200
ORDER BY random()
LIMIT $2
"""


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--n", type=int, default=40)
    args = ap.parse_args()

    s = get_settings()
    client = make_client(s)
    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
            if coll_id is None:
                print(f"集合 {args.collection} 不存在")
                return
            blocks = await conn.fetch(_BLOCKS_SQL, coll_id, args.n)
    finally:
        await close_pool()

    sem = asyncio.Semaphore(s.fact_concurrency)
    rows: list[dict] = []

    async def one(b) -> None:
        try:
            async with sem:
                resp = await client.chat.completions.create(
                    model=s.fact_model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user",
                         "content": f"标题路径：{' > '.join(b['heading_path'])}\n\n"
                                    f"正文：\n{b['content'][:2000]}"},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.3,
                    extra_body={"enable_thinking": False},
                )
            query = json.loads(resp.choices[0].message.content or "{}").get("query", "").strip()
            if len(query) >= 4:
                rows.append({"query": query, "kind": "answerable",
                             "gold_chunks": b["chunk_id"],
                             "note": " > ".join(b["heading_path"])})
        except Exception as e:
            log.warning("生成失败 {}：{!r}", b["chunk_id"][:8], e)

    await asyncio.gather(*(one(b) for b in blocks))

    path = Path("eval") / f"{args.collection}.draft.csv"
    path.parent.mkdir(exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["query", "kind", "gold_chunks", "note"])
        w.writeheader()
        w.writerows(sorted(rows, key=lambda r: r["note"]))

    print(f"\n取了 {len(blocks)} 个父块，生成 {len(rows)} 条草稿 → {path}")
    if not rows:
        print("❌ 一条都没生成，先看日志里的报错")
    else:
        print("下一步：人工筛一遍（见 7.2.5），保留 30 条左右，再补上无答案问题")


if __name__ == "__main__":
    asyncio.run(main())