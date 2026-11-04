"""导入标注集：CSV 整体替换这个集合的评测问题。

用法：uv run python -m quarry.scripts.import_eval_set [--file eval/travel-guide.csv]
文件名（不含扩展名）就是集合短名。
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from quarry.eval.dataset import load_csv
from quarry.infra.db import acquire, close_pool, init_pool


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="eval/travel-guide.csv")
    args = ap.parse_args()

    path = Path(args.file)
    rows, errors = load_csv(path)
    if errors:
        print(f"❌ {path} 有 {len(errors)} 处错误，一条都没有导入：")
        for err in errors:
            print("   " + err)
        return

    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                path.stem)
            if coll_id is None:
                print(f"集合 {path.stem} 不存在：文件名要和集合短名一致")
                return

            # 金块必须是这个集合里真实存在的父块，否则召回率会被算错
            gold = sorted({g for r in rows for g in r["gold_chunks"]})
            alive = {r["chunk_id"] for r in await conn.fetch(
                "SELECT chunk_id FROM chunks WHERE collection_id = $1"
                " AND chunk_id = ANY($2::text[]) AND level = 0", coll_id, gold)}
            missing = [(r["query"], g) for r in rows for g in r["gold_chunks"]
                       if g not in alive]
            if missing:
                print(f"❌ {len(missing)} 个金块在库里找不到，一条都没有导入：")
                for query, g in missing[:10]:
                    print(f"   {g[:12]}…  {query}")
                print("   语料重新接入之后块 ID 会变，重新生成草稿即可")
                return

            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM eval_queries WHERE collection_id = $1", coll_id)
                await conn.executemany(
                    """INSERT INTO eval_queries (collection_id, qid, query, kind,
                                                 gold_chunks, note)
                       VALUES ($1, $2, $3, $4, $5, $6)""",
                    [(coll_id, r["qid"], r["query"], r["kind"], r["gold_chunks"], r["note"])
                     for r in rows])
    finally:
        await close_pool()

    answerable = [r for r in rows if r["kind"] == "answerable"]
    print(f"已导入 {len(rows)} 条：有答案 {len(answerable)}，"
          f"无答案 {len(rows) - len(answerable)}，金块 {len(gold)} 个")
    if len(answerable) < 20 or len(rows) - len(answerable) < 5:
        print("⚠️ 建议至少 20 条有答案、5 条无答案，指标才有参考价值")


if __name__ == "__main__":
    asyncio.run(main())