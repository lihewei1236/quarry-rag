"""空实现。对照组，也是默认档。"""
from __future__ import annotations

from quarry.retrieval.base import Hit


class NoopReranker:
    """什么都不做，按原顺序返回前 top_k 条。

    它存在的理由不是"还没实现"，是"需要一个对照组"。
    5.3 的开关对照、第七章的对照实验，都要拿它当基准。
    """

    name = "noop"

    async def rerank(self, query: str, hits: list[Hit],
                     top_k: int) -> list[Hit]:
        return hits[:top_k]        # rerank_score 保持 None，见协议第 2 条