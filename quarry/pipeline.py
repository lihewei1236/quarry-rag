"""检索链路总装：召回 → 重排 → 同节去重 → 上下文 → 事实层 → 引用 → 拒答信号。

全项目唯一允许 import 所有子包的模块，任何子包都不许反过来 import 它。
第八章的接口只调 search_evidence。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from quarry.common.timing import StageTimer
from quarry.context.abstain import AbstainSignal, judge
from quarry.context.citation import build_citation, fetch_documents
from quarry.context.expand import (apply_budget, context_options, dedupe_by_parent,
                                   fetch_neighbors, fetch_parents)
from quarry.context.models import Evidence
from quarry.facts.attach import FactMasker, fetch_mentions, verified_date
from quarry.infra.db import acquire
from quarry.infra.settings import get_settings
from quarry.rerank import get_reranker
from quarry.rerank.noop import NoopReranker
from quarry.retrieval.base import Hit
from quarry.retrieval.pg.searcher import hybrid_search


@dataclass
class SearchResult:
    query: str
    evidences: list[Evidence]
    abstain: AbstainSignal
    timings: dict[str, float]
    debug: dict = field(default_factory=dict)


async def search_evidence(
    collection_slug: str,
    query: str,
    caller_labels: list[str],
    meta_filter: dict | None = None,
    top_k: int | None = None,
    *,
    use_rerank: bool = True,
    use_facts: bool = True,
) -> SearchResult:
    s = get_settings()
    top_k = top_k or s.default_top_k
    reranker = get_reranker() if use_rerank else NoopReranker()
    t = StageTimer()

    # 1. 查集合，顺带拿到拒答配置和事实层开关
    with t.stage("lookup"):
        async with acquire() as conn:
            coll = await conn.fetchrow(
                """SELECT id, abstain_config, facts_enabled FROM collections
                   WHERE slug = $1 AND status = 'active'""", collection_slug)
    if coll is None:
        raise ValueError(f"集合不存在或未激活：{collection_slug}")
    coll_id: UUID = coll["id"]

    # 2. 召回。多取一些：重排要从里面挑，同节去重也会砍掉一部分
    recall_n = max(top_k * 3, s.rerank_candidates)
    with t.stage("recall"):
        hits = await hybrid_search(
            collection_id=coll_id, query=query, caller_labels=caller_labels,
            meta_filter=meta_filter, dense_k=max(50, recall_n),
            sparse_k=max(50, recall_n), top_k=recall_n)

    # 3. 重排。只把前 rerank_candidates 条送去打分，剩下的按原顺序接在后面
    with t.stage("rerank"):
        head, tail = hits[:s.rerank_candidates], hits[s.rerank_candidates:]
        ranked = await reranker.rerank(query, head, top_k=len(head)) + tail
    reranked = bool(ranked) and ranked[0].rerank_score is not None

    # 4. 同一小节只留一条，再截到 top_k
    picked = dedupe_by_parent(ranked)[:top_k]

    # 5. 回捞父块、邻块和文档信息，借一次连接查完
    with t.stage("expand"):
        async with acquire() as conn:
            parents = await fetch_parents(
                conn, coll_id, [h.parent_id for h, _ in picked], caller_labels)
            neighbors = await fetch_neighbors(
                conn, coll_id, list(parents.values()), s.neighbor_radius, caller_labels)
            docs = await fetch_documents(
                conn, coll_id, [h.document_id for h, _ in picked])

    # 6. 事实层（第六章）：取这些父块里的易变数值。只对打开了开关的集合生效
    masker = None
    if use_facts and coll["facts_enabled"]:
        block_ids = [*parents, *(n["chunk_id"] for near in neighbors.values() for n in near)]
        with t.stage("facts"):
            async with acquire() as conn:
                rows = await fetch_mentions(conn, coll_id, block_ids, caller_labels)
        masker = FactMasker(rows, s.fact_ttl_days)

    # 7. 按预算给每条证据挑上下文档位，组装证据
    with t.stage("assemble"):
        options = [_context_options(h, parents, neighbors, masker) for h, _ in picked]
        chosen = apply_budget(options, s.context_max_chars)
        evidences = [
            Evidence(
                chunk_id=h.chunk_id,
                text=h.content,
                context=ctx,
                context_level=level,
                citation=build_citation(h, docs.get(h.document_id)),
                score=h.score,
                rerank_score=h.rerank_score,
                dense_score=h.dense_score,
                sparse_score=h.sparse_score,
                dense_rank=h.dense_rank,
                sparse_rank=h.sparse_rank,
                sibling_hits=merged,
            )
            for (h, merged), (level, ctx) in zip(picked, chosen)
        ]
        if masker is not None:
            contexts, facts = masker.finalize([e.context for e in evidences])
            for e, ctx, used in zip(evidences, contexts, facts):
                e.context, e.facts = ctx, used
                e.citation.verified_at = verified_date(used)

    # 8. 拒答信号：看召回结果，不看重排后的，开关重排都不用重新标定阈值
    signal = judge(hits, coll["abstain_config"])

    t.mark_total()
    return SearchResult(
        query=query,
        evidences=evidences,
        abstain=signal,
        timings=t.as_dict(),
        debug={
            "candidates": len(hits),
            "rerank_backend": reranker.name,
            "reranked": reranked,
            "facts_enabled": masker is not None,
            "context_chars": sum(len(e.context) for e in evidences),
        },
    )


def _context_options(h: Hit, parents: dict, neighbors: dict,
                     masker: FactMasker | None) -> list[tuple[str, str]]:
    """给一条命中生成三档上下文。开了事实层时，先把父块、邻块、命中块里的数值遮住。"""
    parent = parents.get(h.parent_id or "")
    near = neighbors.get(h.parent_id or "", [])
    chunk_text = h.content
    if masker is not None and parent is not None:
        # 命中块是父块的一段，它在父块里的偏移 = 两者在原文里起点的差
        chunk_text = masker.mask(parent["chunk_id"], h.content,
                                 offset=h.char_start - parent["char_start"])
        near = [{**n, "content": masker.mask(n["chunk_id"], n["content"])} for n in near]
        parent = {**parent, "content": masker.mask(parent["chunk_id"], parent["content"])}
    return context_options(parent, near, chunk_text)