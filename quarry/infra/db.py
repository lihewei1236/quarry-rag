"""asyncpg 连接池。全进程共用一个。"""
import json
from contextlib import asynccontextmanager

import asyncpg

from quarry.infra.settings import get_settings

_pool: asyncpg.Pool | None = None


async def _init_conn(conn: asyncpg.Connection) -> None:
    """每条新连接建立时执行：让 asyncpg 自动在 dict 和 JSON / JSONB 之间转换。"""
    for typ in ("json", "jsonb"):
        await conn.set_type_codec(
            typ, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )


async def init_pool() -> asyncpg.Pool:
    """创建连接池。已经建过就直接返回，不会建第二个。"""
    global _pool
    if _pool is None:
        s = get_settings()
        _pool = await asyncpg.create_pool(
            dsn=s.dsn,
            min_size=s.pool_min_size,
            max_size=s.pool_max_size,
            statement_cache_size=0,
            command_timeout=30,
            init=_init_conn,
        )
    return _pool


def get_pool() -> asyncpg.Pool:
    """取连接池。没初始化就报错，不隐式创建。"""
    if _pool is None:
        raise RuntimeError("连接池未初始化，先在应用启动钩子里 await init_pool()")
    return _pool


async def close_pool() -> None:
    """关闭连接池，应用退出时调用。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def acquire():
    """借一条连接，退出 with 块时自动归还。业务代码只用这个。"""
    async with get_pool().acquire() as conn:
        yield conn