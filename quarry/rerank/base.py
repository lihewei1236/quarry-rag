"""Reranker 协议。"""
from __future__ import annotations

from typing import Protocol

from quarry.retrieval.base import Hit


class Reranker(Protocol):
    """所有重排实现都要守住三条：

    1. 只调整顺序，不修改命中的内容与区间；返回不超过 top_k 条。
    2. 真的重排过，才把分数写进 hit.rerank_score；没重排过就保持 None。
    3. 不许抛异常。接口失败时写一条 warning，按原顺序返回前 top_k 条。
    """

    name: str

    async def rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]: ...