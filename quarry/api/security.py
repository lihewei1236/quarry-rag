"""鉴权与限流：把一把 key 换成一个调用方，并挡住打得太快的请求。"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Header, HTTPException, Request

from quarry.infra.db import acquire

_CALLER_SQL = """
SELECT key_id, caller_id, labels, collections, rate_limit
FROM api_keys
WHERE key_hash = $1 AND status = 'active'
"""


@dataclass(frozen=True)
class Caller:
    """一个调用方。labels 是服务端给的，请求里带什么都改不了它。"""

    key_id: str
    caller_id: str
    labels: list[str]
    collections: list[str]
    rate_limit: int


def hash_key(raw: str) -> str:
    """库里只存哈希：备份泄露、误看数据库，都拿不到一把能用的 key。"""
    return hashlib.sha256(raw.strip().encode("utf-8")).hexdigest()


def minute_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y%m%d%H%M")


async def fetch_caller(conn, raw_key: str) -> Caller | None:
    row = await conn.fetchrow(_CALLER_SQL, hash_key(raw_key))
    if row is None:
        return None
    return Caller(key_id=row["key_id"], caller_id=row["caller_id"],
                  labels=list(row["labels"]), collections=list(row["collections"]),
                  rate_limit=row["rate_limit"])


async def count_request(redis, caller: Caller, now: datetime | None = None) -> int:
    """固定窗口计数：每分钟一个键，自增之后设过期。返回这一分钟里的第几次。"""
    key = f"quarry:rl:{caller.key_id}:{minute_key(now)}"
    used = int(await redis.incr(key))
    if used == 1:
        await redis.expire(key, 120)
    return used


async def require_caller(request: Request,
                         authorization: str = Header(default="")) -> Caller:
    """所有业务接口都挂这个依赖：没有它，就等于没有权限模型。"""
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "缺少 API Key：请在 Authorization 头里带 Bearer <key>")
    async with acquire() as conn:
        caller = await fetch_caller(conn, authorization[7:])
    if caller is None:
        raise HTTPException(401, "API Key 无效或已停用")

    used = await count_request(request.app.state.redis, caller)
    if used > caller.rate_limit:
        raise HTTPException(429, f"超出每分钟 {caller.rate_limit} 次的限制",
                            headers={"Retry-After": "60"})
    request.state.caller = caller
    return caller