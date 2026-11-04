"""引用构造。"""
from __future__ import annotations

from uuid import UUID

from quarry.context.models import Citation
from quarry.retrieval.base import Hit

# 解析档位决定引用能精确到什么程度
_LOCATOR = {"A": "char", "B": "paragraph"}

_DOCS_SQL = """
SELECT id, source_uri, title, parser_tier, meta
FROM documents
WHERE collection_id = $1 AND id = ANY($2::uuid[])
"""


async def fetch_documents(conn, collection_id: UUID,
                          doc_ids: list[UUID]) -> dict[UUID, dict]:
    """批量取文档信息，返回 {文档 ID: 行}。"""
    ids = list(dict.fromkeys(doc_ids))
    if not ids:
        return {}
    rows = await conn.fetch(_DOCS_SQL, collection_id, ids)
    return {r["id"]: dict(r) for r in rows}


def build_citation(hit: Hit, doc: dict | None) -> Citation:
    """文档信息拿不到时也返回一个能用的引用，宁可少几个字段，也不丢掉整条证据。"""
    doc = doc or {}
    meta = doc.get("meta") or {}
    return Citation(
        document_id=hit.document_id,
        source_uri=doc.get("source_uri") or "",
        title=doc.get("title"),
        url=meta.get("url"),
        license=meta.get("license"),
        heading_path=list(hit.heading_path),
        char_start=hit.char_start,
        char_end=hit.char_end,
        locator_kind=_LOCATOR.get(doc.get("parser_tier"), "unknown"),
    )