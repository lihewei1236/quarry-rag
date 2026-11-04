"""PostgreSQL 检索实现。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import UUID

from quarry.common.vectors import to_halfvec_literal
from quarry.infra.db import acquire
from quarry.infra.embed import get_embedder
from quarry.infra.executor import get_cpu_executor
from quarry.retrieval.base import Hit
from quarry.retrieval.query import build_tsquery

_SQL_DIR = Path(__file__).parent / "sql"
_HYBRID_SQL = (_SQL_DIR / "hybrid_search.sql").read_text(encoding="utf-8")


async def hybrid_search(
    collection_id: UUID,
    query: str,
    caller_labels: list[str],
    meta_filter: dict | None = None,
    dense_k: int = 50,
    sparse_k: int = 50,
    weight_dense: float = 0.5,
    weight_sparse: float = 0.5,
    rrf_k: int = 60,
    top_k: int = 10,
) -> list[Hit]:
    """混合检索，返回按融合分数排序的命中列表。"""
    meta_filter = meta_filter or {}
    loop = asyncio.get_running_loop()

    # 向量化是网络 I/O，分词是 CPU，两件事同时做。
    # 分词丢进线程池的原因见 4.7。
    qvec_list, tsq = await asyncio.gather(
        get_embedder().embed([query]),
        loop.run_in_executor(get_cpu_executor(), build_tsquery, query),
    )
    vec_lit = to_halfvec_literal(qvec_list[0])

    async with acquire() as conn, conn.transaction():
        # SET LOCAL 只在本事务内生效，不会污染连接池里的这条连接。
        # 为什么要开迭代扫描，见 4.5。
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        await conn.execute("SET LOCAL hnsw.max_scan_tuples = 20000")
        rows = await conn.fetch(
            _HYBRID_SQL,
            collection_id, caller_labels, meta_filter, vec_lit, tsq,
            dense_k, sparse_k, weight_dense, weight_sparse, rrf_k, top_k,
        )

    return [Hit(
        chunk_id=r["chunk_id"],
        document_id=r["document_id"],
        parent_id=r["parent_id"],
        heading_path=list(r["heading_path"] or []),
        content=r["content"],
        char_start=r["char_start"],
        char_end=r["char_end"],
        score=float(r["score"]),
        dense_rank=r["dense_rank"],
        sparse_rank=r["sparse_rank"],
        dense_score=float(r["dense_score"]) if r["dense_score"] is not None else None,
        sparse_score=float(r["sparse_score"]) if r["sparse_score"] is not None else None,
    ) for r in rows]


class PgRetriever:
    """把 hybrid_search 包成符合 Retriever 协议的对象。见 4.6。"""

    async def search(
        self,
        collection_id: UUID,
        query: str,
        caller_labels: list[str],
        meta_filter: dict | None = None,
        top_k: int = 10,
    ) -> list[Hit]:
        return await hybrid_search(
            collection_id=collection_id, query=query,
            caller_labels=caller_labels, meta_filter=meta_filter, top_k=top_k)