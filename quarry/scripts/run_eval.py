"""跑一次评测，把配置和指标存进 eval_runs。

用法：
    uv run python -m quarry.scripts.run_eval --label baseline --no-rerank --no-facts
    uv run python -m quarry.scripts.run_eval --label full --judge
"""
from __future__ import annotations

import argparse
import asyncio

from quarry.eval.metrics import render
from quarry.eval.runner import run_eval
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.settings import get_settings


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--label", required=True, help="这次跑的名字，比如 baseline、rerank-on")
    ap.add_argument("--top-k", type=int)
    ap.add_argument("--no-rerank", action="store_true")
    ap.add_argument("--no-facts", action="store_true")
    ap.add_argument("--judge", action="store_true", help="调裁判模型评证据够不够，会花钱")
    args = ap.parse_args()

    s = get_settings()
    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
        if coll_id is None:
            print(f"集合 {args.collection} 不存在")
            return
        run_id, metrics, _ = await run_eval(
            s, args.collection, coll_id, args.label,
            top_k=args.top_k or s.eval_top_k,
            use_rerank=not args.no_rerank, use_facts=not args.no_facts,
            use_judge=args.judge)
    finally:
        await close_pool()

    print(f"\n运行 {run_id}")
    print(render(metrics))
    print(f"\n对照另一次运行：uv run python -m quarry.scripts.compare_eval "
          f"--base 别的label --new {args.label}")


if __name__ == "__main__":
    asyncio.run(main())