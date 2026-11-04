"""Redis Stream 生产者。"""
from __future__ import annotations

from uuid import UUID

import redis.asyncio as aioredis

from quarry.infra.settings import get_settings

STREAM_KEY = "quarry:ingest"
DEAD_KEY = "quarry:ingest:dead"
GROUP_NAME = "ingest-workers"

_client: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(
            get_settings().redis_url,
            decode_responses=True,
            socket_connect_timeout=5,    # 建连超时：Redis 没起来时 5 秒就报错
            socket_timeout=None,         # 读超时交给 XREADGROUP 的 block 参数控制
            health_check_interval=30,    # 空闲连接定期保活
        )
    return _client


async def ensure_group() -> None:
    """确保消费者组存在，已存在就跳过。"""
    r = get_redis()
    try:
        await r.xgroup_create(STREAM_KEY, GROUP_NAME, id="0", mkstream=True)
    except aioredis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


async def enqueue_document(document_id: UUID, stage: str = "parsing") -> str:
    r = get_redis()
    return await r.xadd(STREAM_KEY, {
        "document_id": str(document_id),
        "stage": stage,
    })