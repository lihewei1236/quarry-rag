"""对外契约：请求怎么校验，响应长什么样。

响应用普通字典而不是 pydantic 模型：形状由 to_payload() 一个函数决定，
字段和第五、六章的 Evidence 一一对应，不会被中间层悄悄改名。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """请求体。注意这里没有权限字段：调用方标签由 API Key 决定（见 8.3）。"""

    collection: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=500)
    top_k: int | None = Field(default=None, ge=1, le=50)
    filters: dict[str, Any] | None = None
    use_rerank: bool = True
    use_facts: bool = True


def _iso(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _citation(c) -> dict:
    return {"document_id": str(c.document_id), "source_uri": c.source_uri, "title": c.title,
            "url": c.url, "license": c.license, "heading_path": list(c.heading_path),
            "char_start": c.char_start, "char_end": c.char_end,
            "locator_kind": c.locator_kind, "verified_at": _iso(c.verified_at)}


def _fact(f) -> dict:
    return {"ref": f.ref, "entity": f.entity, "attribute": f.attribute, "label": f.label,
            "qualifier": f.qualifier, "value": f.value, "status": f.status,
            "verified_at": _iso(f.verified_at), "source_url": f.source_url,
            "written_values": list(f.written_values)}


def _evidence(e) -> dict:
    return {"chunk_id": e.chunk_id, "text": e.text, "context": e.context,
            "context_level": e.context_level, "citation": _citation(e.citation),
            "facts": [_fact(f) for f in e.facts],
            "score": e.score, "rerank_score": e.rerank_score,
            "dense_score": e.dense_score, "sparse_score": e.sparse_score,
            "sibling_hits": e.sibling_hits}


def to_payload(result, request_id: str) -> dict:
    a = result.abstain
    return {
        "request_id": request_id,
        "query": result.query,
        "abstain": {"should_abstain": a.should_abstain, "reason": a.reason,
                    "top_dense_score": a.top_dense_score, "threshold": a.threshold},
        "evidences": [_evidence(e) for e in result.evidences],
        "timings": dict(result.timings),
    }


def error_payload(code: str, message: str, request_id: str | None) -> dict:
    """错误也有固定形状：上游按 code 分支，按 request_id 找日志。"""
    return {"error": {"code": code, "message": message, "request_id": request_id}}


def collections_payload(rows: list) -> dict:
    return {"collections": [{"slug": r["slug"], "title": r["title"],
                             "facts_enabled": r["facts_enabled"]} for r in rows]}


STATUS_CODES: dict[int, str] = {
    400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found",
    422: "invalid_request", 429: "rate_limited", 500: "internal_error",
    503: "unavailable", 504: "timeout",
}


def code_of(status: int) -> str:
    return STATUS_CODES.get(status, "error")


def redact(query: str, limit: int = 500) -> str:
    """日志里的问题截断，别把超长输入原样堆进库。"""
    return query if len(query) <= limit else query[:limit] + "…"