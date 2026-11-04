"""Hit 模型与 Retriever 协议。整个项目只有这一份 Hit 定义。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID


@dataclass
class Hit:
    """一条检索命中，也就是本项目所说的"一份证据"的原料。"""

    chunk_id: str
    document_id: UUID
    parent_id: str | None
    heading_path: list[str]
    content: str
    char_start: int
    char_end: int
    score: float                  # RRF 融合分
    dense_rank: int | None        # 在向量路的名次，没上榜是 None
    sparse_rank: int | None       # 在全文路的名次
    dense_score: float | None     # 余弦相似度，调试用；第五章拿它判断拒答
    sparse_score: float | None    # ts_rank_cd，调试用
    sparse_score: float | None  # ts_rank_cd，调试用
    rerank_score: float | None = None  # ← 第五章新增：重排分，没重排过就是 None

class Retriever(Protocol):
    async def search(
        self,
        collection_id: UUID,
        query: str,
        caller_labels: list[str],
        meta_filter: dict | None = None,
        top_k: int = 10,
    ) -> list[Hit]: ...