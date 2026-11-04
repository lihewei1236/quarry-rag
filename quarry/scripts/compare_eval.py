"""对照两次评测：看指标差多少，以及哪几个问题变好、哪几个变坏。

用法：
    uv run python -m quarry.scripts.compare_eval --base baseline --new rerank-on
    uv run python -m quarry.scripts.compare_eval --base baseline --new rerank-on --gate
--base / --new 可以写 label（取最近一次）或完整的 run_id。
加 --gate 时，指标掉太多会以非 0 退出。
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from quarry.eval.metrics import LABELS, gate_check, pad
from quarry.infra.db import acquire, close_pool, init_pool

_RUN_SQL = """
SELECT run_id, label, config, metrics, created_at
FROM eval_runs
WHERE collection_id = $1 AND (run_id = $2 OR label = $2)
ORDER BY created_at DESC
LIMIT 1
"""

_RESULTS_SQL = """
SELECT r.qid, r.hit_rank, q.query, q.kind
FROM eval_results r
JOIN eval_queries q ON q.qid = r.qid AND q.collection_id = $2
WHERE r.run_id = $1
"""


def _conf(row) -> str:
    c = row["config"]
    return (f"rerank={c.get('use_rerank')} facts={c.get('use_facts')} "
            f"top_k={c.get('top_k')} judge={c.get('judge_model') or '-'}")


def _delta(base, new) -> str:
    if base is None or new is None:
        return "-"
    d = new - base
    return f"{d:+.4f}" if abs(d) < 100 else f"{d:+.1f}"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--gate", action="store_true")
    args = ap.parse_args()

    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
            base = await conn.fetchrow(_RUN_SQL, coll_id, args.base)
            new = await conn.fetchrow(_RUN_SQL, coll_id, args.new)
            if base is None or new is None:
                print(f"找不到运行记录：{args.base if base is None else args.new}")
                sys.exit(2)
            base_items = {r["qid"]: r for r in
                          await conn.fetch(_RESULTS_SQL, base["run_id"], coll_id)}
            new_items = {r["qid"]: r for r in
                         await conn.fetch(_RESULTS_SQL, new["run_id"], coll_id)}
    finally:
        await close_pool()

    print(f"基线  {base['run_id']}  {_conf(base)}")
    print(f"新跑  {new['run_id']}  {_conf(new)}\n")
    print("  " + pad("指标", 32) + pad("基线", 12) + pad("新跑", 12) + "变化")
    for key, label in LABELS.items():
        b, n = base["metrics"].get(key), new["metrics"].get(key)
        print("  " + pad(label, 32) + pad("-" if b is None else str(b), 12)
              + pad("-" if n is None else str(n), 12) + _delta(b, n))

    better, worse = [], []
    for qid, nrow in new_items.items():
        brow = base_items.get(qid)
        if brow is None or nrow["kind"] != "answerable":
            continue
        b, n = brow["hit_rank"], nrow["hit_rank"]
        if b is None and n is not None:
            better.append((nrow["query"], "未命中", f"第 {n}"))
        elif b is not None and n is None:
            worse.append((nrow["query"], f"第 {b}", "未命中"))
        elif b is not None and n is not None and n < b:
            better.append((nrow["query"], f"第 {b}", f"第 {n}"))
        elif b is not None and n is not None and n > b:
            worse.append((nrow["query"], f"第 {b}", f"第 {n}"))

    for title, group in (("变好", better), ("变坏", worse)):
        print(f"\n{title}的问题（{len(group)} 条，最多显示 10 条）：")
        for query, before, after in group[:10]:
            print("  " + pad(query[:28], 34) + f"{before} → {after}")

    if args.gate:
        problems = gate_check(base["metrics"], new["metrics"])
        if problems:
            print("\n回归闸门：❌ 不通过")
            for p in problems:
                print("  " + p)
            sys.exit(1)
        print("\n回归闸门：✅ 通过")


if __name__ == "__main__":
    asyncio.run(main())