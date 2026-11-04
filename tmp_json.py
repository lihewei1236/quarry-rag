import asyncio

from quarry.infra.db import acquire, close_pool, init_pool


async def main():
    await init_pool()
    try:
        async with acquire() as conn:
            v = await conn.fetchval("SELECT $1::jsonb", {"city": "西安"})
            print(type(v).__name__, v)
    finally:
        await close_pool()


asyncio.run(main())