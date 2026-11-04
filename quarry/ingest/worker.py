"""接入 worker。从 Redis Stream 读消息，跑 3.4 的接入管道。

运行：uv run python -m quarry.ingest.worker
"""
from __future__ import annotations

import asyncio
import os
import socket
import time
import uuid

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.logging import get_logger
from quarry.ingest.queue import DEAD_KEY, GROUP_NAME, STREAM_KEY, ensure_group, get_redis

log = get_logger()

# 名字要在整个集群里唯一：主机名 + 进程号 + 一段随机
CONSUMER_NAME = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"

CLAIM_INTERVAL = 60                  # 每 60 秒扫一次 PEL
CLAIM_MIN_IDLE_MS = 5 * 60 * 1000    # 空闲 5 分钟以上的才认领
MAX_DELIVERIES = 3


async def run_worker() -> None:
    await init_pool()
    await ensure_group()
    r = get_redis()
    log.info("worker {} 启动，监听 {}", CONSUMER_NAME, STREAM_KEY)

    last_claim = time.monotonic()
    try:
        while True:
            if time.monotonic() - last_claim > CLAIM_INTERVAL:
                await _claim_stale(r)
                last_claim = time.monotonic()

            resp = await r.xreadgroup(
                GROUP_NAME, CONSUMER_NAME,
                {STREAM_KEY: ">"},               # ">" 表示只要还没分给任何人的新消息
                count=1, block=5000,             # 最多阻塞 5 秒
            )
            if not resp:
                continue

            for _stream, messages in resp:
                for msg_id, fields in messages:
                    await _handle(r, msg_id, fields)
    finally:
        await close_pool()


async def _handle(r, msg_id: str, fields: dict) -> None:
    doc_id = fields.get("document_id")
    stage = fields.get("stage", "parsing")
    log.info("[{}] 文档 {} 阶段 {}", msg_id, doc_id, stage)

    try:
        await _do_work(doc_id, stage)
        await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
        log.info("[{}] 完成", msg_id)
    except Exception as e:
        log.exception("[{}] 失败 {}", msg_id, e)
        await _mark_failed(doc_id, str(e)[:500])
        # 故意不 XACK：消息留在 PEL 里，等超时后被认领重试


async def _do_work(doc_id: str, stage: str) -> None:
    """按 document_id 找到文件，跑一遍 3.4 的接入管道。

    本章把整条管道放在一个阶段里做完（见 3.5.5）。
    worker 是一个入口程序，和 scripts 同一层，所以这里直接复用接入脚本的 ingest_file。
    放在函数里 import，是为了不让 quarry.ingest 包在被导入时就依赖 scripts。
    """
    from quarry.scripts.ingest_corpus import CORPUS_ROOT, ingest_file, load_manifest

    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT d.source_uri, d.collection_id, c.slug,
                      c.default_acl_labels, c.chunk_policy
               FROM documents d JOIN collections c ON c.id = d.collection_id
               WHERE d.id = $1""", doc_id)
    if row is None:
        raise ValueError(f"文档不存在 {doc_id}")

    path = CORPUS_ROOT / row["source_uri"]
    if not path.exists():
        raise FileNotFoundError(str(path))

    await ingest_file(path, row["collection_id"], row["slug"],
                      list(row["default_acl_labels"]), load_manifest(),
                      row["chunk_policy"] or None)


async def _mark_failed(doc_id: str | None, err: str) -> None:
    if not doc_id:
        return
    async with acquire() as conn:
        await conn.execute(
            "UPDATE documents SET status = 'failed', stage_error = $1 WHERE id = $2",
            err, doc_id)


async def _claim_stale(r) -> None:
    """扫 PEL：长时间没销账的消息认领过来重做，超过上限的转死信。"""
    pending = await r.xpending_range(
        STREAM_KEY, GROUP_NAME,
        min="-", max="+", count=10,
        idle=CLAIM_MIN_IDLE_MS,      # 注意参数名是 idle，不是 idletime
    )
    for p in pending:
        msg_id = p["message_id"]
        delivered = p["times_delivered"]

        if delivered >= MAX_DELIVERIES:
            log.error("[{}] 投递 {} 次仍失败，转死信", msg_id, delivered)
            fields = await r.xrange(STREAM_KEY, min=msg_id, max=msg_id)
            doc_id = fields[0][1].get("document_id") if fields else None
            await r.xadd(DEAD_KEY, {"origin_id": msg_id, "document_id": doc_id or "",
                                    "times_delivered": str(delivered)})
            await _mark_failed(doc_id, f"投递 {delivered} 次仍失败，已转入死信")
            await r.xack(STREAM_KEY, GROUP_NAME, msg_id)
            continue

        claimed = await r.xclaim(
            STREAM_KEY, GROUP_NAME, CONSUMER_NAME,
            min_idle_time=CLAIM_MIN_IDLE_MS, message_ids=[msg_id])
        if claimed:
            log.warning("[{}] 认领超时消息，这是第 {} 次投递", msg_id, delivered + 1)
            for cid, fields in claimed:
                await _handle(r, cid, fields)


if __name__ == "__main__":
    asyncio.run(run_worker())