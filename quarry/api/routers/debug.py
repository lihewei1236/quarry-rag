"""检索调试接口。只在开发环境开放。"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter, HTTPException

from quarry.common.vectors import to_halfvec_literal
from quarry.infra.db import acquire
from quarry.infra.embed import get_embedder
from quarry.infra.executor import get_cpu_executor
from quarry.infra.settings import get_settings
from quarry.retrieval.query import build_tsquery

router = APIRouter(prefix="/debug", tags=["debug"])

_SQL = (Path(__file__).resolve().parents[2]
        / "retrieval" / "pg" / "sql" / "debug_search.sql").read_text(encoding="utf-8")


@router.post("/search")
async def debug_search(payload: dict):
    """返回三路结果加耗时归因。"""
    if get_settings().app_env != "development":
        raise HTTPException(403, "调试接口仅开发环境可用")

    query = (payload.get("query") or "").strip()
    if not query:
        raise HTTPException(400, "query 不能为空")

    slug = payload.get("collection", "travel-guide")
    labels = payload.get("caller_labels") or ["public"]
    meta_filter = payload.get("meta_filter") or {}
    top_k = int(payload.get("top_k", 10))
    w_dense = float(payload.get("weight_dense", 0.5))
    w_sparse = float(payload.get("weight_sparse", 0.5))
    rrf_k = int(payload.get("rrf_k", 60))

    timings: dict[str, float] = {}
    loop = asyncio.get_running_loop()

    t0 = time.perf_counter()
    async with acquire() as conn:
        coll_id = await conn.fetchval(
            "SELECT id FROM collections WHERE slug = $1 AND status = 'active'", slug)
    if coll_id is None:
        raise HTTPException(404, f"集合不存在 {slug}")
    timings["lookup_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    qvec_list, tsq = await asyncio.gather(
        get_embedder().embed([query]),
        loop.run_in_executor(get_cpu_executor(), build_tsquery, query),
    )
    timings["prepare_ms"] = (time.perf_counter() - t0) * 1000

    t0 = time.perf_counter()
    async with acquire() as conn, conn.transaction():
        await conn.execute("SET LOCAL hnsw.iterative_scan = relaxed_order")
        await conn.execute("SET LOCAL hnsw.max_scan_tuples = 20000")
        row = await conn.fetchrow(
            _SQL, coll_id, labels, meta_filter,
            to_halfvec_literal(qvec_list[0]), tsq,
            50, 50, w_dense, w_sparse, rrf_k, top_k)
    timings["sql_ms"] = (time.perf_counter() - t0) * 1000
    timings["total_ms"] = sum(timings.values())

    return {
        "query": query,
        "tsquery": tsq,                 # 把拼出来的 tsquery 也显示出来
        "dense": row["dense"] or [],
        "sparse": row["sparse"] or [],
        "merged": row["merged"] or [],
        "timings": timings,
    }