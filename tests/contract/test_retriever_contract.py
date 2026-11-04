"""契约测试。任何 Retriever 实现都要满足这几条，包括 Fake。"""
from uuid import uuid4

import pytest

from quarry.retrieval.base import Hit
from quarry.retrieval.fake import FakeRetriever


def make_hits(n: int = 5) -> list[Hit]:
    return [Hit(
        chunk_id=f"c{i}", document_id=uuid4(), parent_id=f"p{i}",
        heading_path=["某节"], content=f"内容 {i}",
        char_start=0, char_end=10,
        score=1.0 - i * 0.1,
        dense_rank=i + 1 if i < 3 else None,
        sparse_rank=i + 1 if i > 1 else None,
        dense_score=0.9 - i * 0.1 if i < 3 else None,
        sparse_score=0.2 - i * 0.01 if i > 1 else None,
    ) for i in range(n)]


@pytest.fixture
def retriever():
    """想把这套契约用在 PgRetriever 上，改这一个 fixture 就行。"""
    return FakeRetriever(make_hits())


async def test_never_returns_more_than_top_k(retriever):
    result = await retriever.search(uuid4(), "测试", ["public"], top_k=3)
    assert len(result) <= 3


async def test_results_are_sorted_by_score_desc(retriever):
    result = await retriever.search(uuid4(), "测试", ["public"], top_k=5)
    scores = [h.score for h in result]
    assert scores == sorted(scores, reverse=True)


async def test_every_hit_has_usable_evidence_fields(retriever):
    """第一章说过，一份证据的最小定义有五样东西，少一样上游就用不了。"""
    for h in await retriever.search(uuid4(), "测试", ["public"]):
        assert h.chunk_id
        assert isinstance(h.content, str) and h.content
        assert h.heading_path is not None
        assert h.char_end > h.char_start
        assert h.score is not None


async def test_at_least_one_rank_is_present(retriever):
    """一条命中至少要被某一路召回过，两路都是 None 说明融合写错了。"""
    for h in await retriever.search(uuid4(), "测试", ["public"]):
        assert h.dense_rank is not None or h.sparse_rank is not None