"""把两次评测运行渲染成一个 HTML 报告，双击即可打开，也可以直接发给同事。

用法：uv run python -m quarry.scripts.render_report --base baseline --new full
"""
from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from quarry.eval.report import build, render
from quarry.infra.db import acquire, close_pool, init_pool


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="travel-guide")
    ap.add_argument("--base", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", help="输出路径，默认 eval/report-<对照 run_id>.html")
    args = ap.parse_args()

    await init_pool()
    try:
        async with acquire() as conn:
            coll_id = await conn.fetchval(
                "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
                args.collection)
            if coll_id is None:
                print(f"集合 {args.collection} 不存在")
                return
            data = await build(conn, coll_id, args.collection, args.base, args.new)
    except ValueError as e:
        print(f"❌ {e}")
        return
    finally:
        await close_pool()

    out = Path(args.out) if args.out else Path("eval") / f"report-{data['new']['run_id']}.html"
    render(data, out)
    size = out.stat().st_size / 1024
    print(f"报告已生成：{out}（{size:.0f} KB，{len(data['items'])} 题）")
    print(f"闸门：{'✅ 通过' if data['gate']['ok'] else '❌ ' + '；'.join(data['gate']['problems'])}")
    print("直接用浏览器打开即可，不需要起服务")


if __name__ == "__main__":
    asyncio.run(main())