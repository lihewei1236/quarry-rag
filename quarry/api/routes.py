"""路由：一个检索端点，一个集合清单端点。

这一层只做三件事：挡住没权限的、把参数转成 search_evidence 的入参、把结果转成 JSON。
任何检索逻辑都不许写在这里。
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request

from quarry.api.logs import log_query
from quarry.api.models import SearchRequest, collections_payload, to_payload
from quarry.api.security import Caller, require_caller
from quarry.infra.db import acquire
from quarry.infra.settings import get_settings
from quarry.pipeline import search_evidence

router = APIRouter(prefix="/v1")

# 数据库里这一列叫 name，对外叫 title；别直接写 SELECT slug, title
_COLLECTIONS_SQL = """
SELECT slug, name AS title, facts_enabled
FROM collections
WHERE status = 'active' AND slug = ANY($1::text[])
ORDER BY slug
"""


@router.post("/search")
async def search(req: SearchRequest, request: Request, background: BackgroundTasks,
                 caller: Caller = Depends(require_caller)) -> dict:
    s = get_settings()
    request_id = request.state.request_id
    top_k = min(req.top_k or s.default_top_k, s.api_max_top_k)

    if req.collection not in caller.collections:
        # 不区分"没有这个集合"和"没有权限"：否则接口会变成一个集合名探测器
        await log_query(request_id=request_id, caller_id=caller.caller_id,
                        collection=req.collection, query=req.query,
                        top_k=top_k, status=403)
        raise HTTPException(403, f"调用方 {caller.caller_id} 无权访问集合 {req.collection}")

    try:
        async with asyncio.timeout(s.api_request_timeout_s):
            result = await search_evidence(
                req.collection, req.query, caller_labels=caller.labels,
                meta_filter=req.filters, top_k=top_k,
                use_rerank=req.use_rerank, use_facts=req.use_facts)
    except TimeoutError:
        await log_query(request_id=request_id, caller_id=caller.caller_id,
                        collection=req.collection, query=req.query,
                        top_k=top_k, status=504)
        raise HTTPException(504, f"检索超过 {s.api_request_timeout_s} 秒未完成")
    except ValueError as e:
        await log_query(request_id=request_id, caller_id=caller.caller_id,
                        collection=req.collection, query=req.query,
                        top_k=top_k, status=404)
        raise HTTPException(404, str(e))

    # 日志放到响应之后写，不占请求时间
    background.add_task(log_query, request_id=request_id, caller_id=caller.caller_id,
                        collection=req.collection, query=req.query, top_k=top_k,
                        status=200, result=result)
    return to_payload(result, request_id)


@router.get("/collections")
async def collections(caller: Caller = Depends(require_caller)) -> dict:
    """上游用它确认自己能访问哪些集合，不用去翻文档。"""
    async with acquire() as conn:
        rows = await conn.fetch(_COLLECTIONS_SQL, caller.collections)
    return collections_payload(rows)