"""导入官方数据：CSV 整体替换这个集合的官方值，然后打印核验状态统计。

用法：uv run python -m quarry.scripts.import_official_facts [--file official/travel-guide.csv]
文件名（不含扩展名）就是集合短名。
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from quarry.facts.contract import ATTRIBUTES
from quarry.facts.official import coverage, load_csv, replace_facts
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.settings import get_settings


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", default="official/travel-guide.csv")
    args = ap.parse_args()

    path = Path(args.file)
    rows, errors = load_csv(path)
    if errors:
        print(f"❌ {path} 有 {len(errors)} 处错误，一行都没有导入：")
        for err in errors:
            print("   " + err)
        return

    s = get_settings()
    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                path.stem)
            if coll_id is None:
                print(f"集合 {path.stem} 不存在：文件名要和集合短名一致")
                return
            await replace_facts(conn, coll_id, rows)
            report = await coverage(conn, coll_id, s.fact_ttl_days)
    finally:
        await close_pool()

    c = report["counts"]
    print(f"已导入官方值    {len(rows)} 条\n")
    print(f"正文提及        {sum(c.values())} 条")
    print(f"  verified      {c['verified']}")
    print(f"  expired       {c['expired']}")
    print(f"  unverified    {c['unverified']}")

    if report["conflicts"]:
        print(f"\n正文与官方不一致（{len(report['conflicts'])} 项）：")
        for (name, attribute, qualifier, written, official), n in report["conflicts"].items():
            print(f"  {name} {ATTRIBUTES[attribute]}·{qualifier}  "
                  f"正文 {written} → 官方 {official}（{n} 处）")
    if report["expired"]:
        print(f"\n超过 {s.fact_ttl_days} 天没有重新核实的官方值：")
        for r in report["expired"]:
            print(f"  {r['entity_name']} {ATTRIBUTES[r['attribute']]}·{r['qualifier']}"
                  f"  核实于 {r['verified_at']}")


if __name__ == "__main__":
    asyncio.run(main())