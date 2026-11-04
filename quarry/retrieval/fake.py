"""FakeRetriever。给契约测试和离线开发用。"""
from __future__ import annotations

from uuid import UUID

from quarry.retrieval.base import Hit


class FakeRetriever:
    """返回预设的命中列表，不连数据库。"""

    def __init__(self, canned: list[Hit] | None = None):
        self._canned = list(canned or [])
        self.calls: list[dict] = []          # 记下每次调用，方便测试里断言

    async def search(
        self,
        collection_id: UUID,
        query: str,
        caller_labels: list[str],
        meta_filter: dict | None = None,
        top_k: int = 10,
    ) -> list[Hit]:
        self.calls.append({
            "collection_id": collection_id, "query": query,
            "caller_labels": list(caller_labels),
            "meta_filter": dict(meta_filter or {}), "top_k": top_k,
        })
        # 和真实现保持同样的约定：按分数降序、不超过 top_k
        ordered = sorted(self._canned, key=lambda h: h.score, reverse=True)
        return ordered[:top_k]

    def set_hits(self, hits: list[Hit]) -> None:
        self._canned = list(hits)