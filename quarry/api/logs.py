"""查询日志：每个请求一行。只记 ID 和指标，不记证据原文。"""
from __future__ import annotations

from uuid import UUID

from quarry.api.models import redact
from quarry.infra.db import acquire
from quarry.infra.logging import get_logger
from quarry.infra.settings import get_settings

log = get_logger()

_SQL = """
INSERT INTO query_logs (request_id, caller_id, collection, query, top_k, status,
                        abstain, evidences, chunk_ids, timings)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
ON CONFLICT (request_id) DO NOTHING
"""


async def log_query(*, request_id: str, caller_id: str, collection: str, query: str,
                    top_k: int, status: int, result=None) -> None:
    """写一条查询日志。写失败只记警告：日志不能反过来弄坏请求。"""
    if not get_settings().api_log_queries:
        return
    try:
        async with acquire() as conn:
            await conn.execute(
                _SQL, UUID(request_id), caller_id, collection, redact(query), top_k, status,
                None if result is None else result.abstain.should_abstain,
                None if result is None else len(result.evidences),
                [] if result is None else [e.chunk_id for e in result.evidences],
                {} if result is None else dict(result.timings))
    except Exception as e:
        log.warning("查询日志写入失败 {}：{!r}", request_id, e)