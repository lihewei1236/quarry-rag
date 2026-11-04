"""给一个上游发一把 API Key。明文只在这里打印一次，库里只存哈希。

用法：
    uv run python -m quarry.scripts.create_api_key --caller zhixing-travel --collections travel-guide --labels public --rate-limit 120
    uv run python -m quarry.scripts.create_api_key --list
    uv run python -m quarry.scripts.create_api_key --revoke 3f9a1c2e
"""
from __future__ import annotations

import argparse
import asyncio
import secrets

from quarry.api.security import hash_key
from quarry.infra.db import acquire, close_pool, init_pool


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--caller", help="调用方名字，例如 zhixing-travel")
    ap.add_argument("--collections", nargs="*", default=[], help="允许访问的集合短名")
    ap.add_argument("--labels", nargs="*", default=["public"], help="这个调用方的权限标签")
    ap.add_argument("--rate-limit", type=int, default=60, help="每分钟请求数上限")
    ap.add_argument("--note")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--revoke", help="按 key_id 停用一把 key")
    args = ap.parse_args()

    await init_pool()
    try:
        async with acquire() as conn:
            if args.list:
                rows = await conn.fetch(
                    """SELECT key_id, caller_id, labels, collections, rate_limit, status, note
                       FROM api_keys ORDER BY created_at""")
                for r in rows:
                    print(f"{r['key_id']}  {r['caller_id']:<18}{r['status']:<10}"
                          f"标签 {','.join(r['labels'])}  "
                          f"集合 {','.join(r['collections']) or '（无）'}"
                          f"  {r['rate_limit']}/分钟  {r['note'] or ''}")
                return
            if args.revoke:
                done = await conn.execute(
                    "UPDATE api_keys SET status = 'disabled' WHERE key_id = $1", args.revoke)
                print("已停用" if done.endswith("1") else f"没有找到 key_id = {args.revoke}")
                return
            if not args.caller:
                print("要么给 --caller 发一把新 key，要么用 --list / --revoke")
                return

            known = {r["slug"] for r in await conn.fetch(
                "SELECT slug FROM collections WHERE status = 'active'")}
            unknown = [c for c in args.collections if c not in known]
            if unknown:
                print(f"❌ 这些集合不存在：{'、'.join(unknown)}")
                return

            raw = "qr_" + secrets.token_urlsafe(32)
            key_id = raw[3:11]
            await conn.execute(
                """INSERT INTO api_keys (key_id, key_hash, caller_id, labels, collections,
                                         rate_limit, note)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)""",
                key_id, hash_key(raw), args.caller, args.labels, args.collections,
                args.rate_limit, args.note)
    finally:
        await close_pool()

    print(f"\n调用方    {args.caller}")
    print(f"key_id    {key_id}")
    print(f"权限标签  {', '.join(args.labels)}")
    print(f"可访问    {', '.join(args.collections) or '（无）'}")
    print(f"限流      {args.rate_limit} 次/分钟")
    print(f"\nAPI Key（只显示这一次，请立刻交给上游并妥善保存）：\n{raw}\n")


if __name__ == "__main__":
    asyncio.run(main())