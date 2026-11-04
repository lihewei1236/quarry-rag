"""挑一份已经在库里的文档，扔进队列。"""
import asyncio

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.ingest.queue import enqueue_document, ensure_group


async def main() -> None:
    await init_pool()
    try:
        await ensure_group()
        async with acquire() as conn:
            doc_id = await conn.fetchval(
                "SELECT id FROM documents WHERE status = 'ready' LIMIT 1")
        if doc_id is None:
            print("库里还没有 ready 的文档，先跑 ingest_corpus")
            return
        msg_id = await enqueue_document(doc_id)
        print(f"入队 {msg_id}，文档 {doc_id}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())