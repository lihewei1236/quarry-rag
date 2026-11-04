"""从打开了事实层的集合里抽取易变数值。

用法：
    uv run python -m quarry.scripts.extract_facts --dry-run --limit 5   # 先看 5 个块，不写库
    uv run python -m quarry.scripts.extract_facts                       # 全量，中断了重跑即可
"""
from __future__ import annotations

import argparse
import asyncio

from quarry.facts.extract import extract_collection
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.settings import get_settings


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--limit", type=int, help="最多处理多少个需要调模型的块")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写数据库")
    args = ap.parse_args()

    s = get_settings()
    await init_pool()
    try:
        async with acquire() as conn:
            coll = await conn.fetchrow(
                "SELECT id, facts_enabled FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
        if coll is None or not coll["facts_enabled"]:
            print(f"集合 {args.collection} 不存在，或者没有打开 facts_enabled（见 6.2.3）")
            return
        st = await extract_collection(s, coll["id"], limit=args.limit, dry_run=args.dry_run)
    finally:
        await close_pool()

    print("\n" + "=" * 50)
    print(f"待处理父块    {st.todo}（粗筛跳过 {st.skipped}）")
    print(f"调用模型      {st.called}，失败 {st.failed}")
    print(f"抽到提及      {st.mentions}")
    if st.dropped:
        print("丢弃          " + "，".join(f"{k} {v}" for k, v in st.dropped.items()))
    if args.dry_run:
        print("（dry-run，没有写入数据库）")
    if st.called and st.failed == st.called:
        print("❌ 模型调用全部失败，往上看日志里的报错")


if __name__ == "__main__":
    asyncio.run(main())