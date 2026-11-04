"""跑一次评测：遍历标注集，调 search_evidence，算指标，把结果写进库。"""
from __future__ import annotations

import time
from datetime import datetime
from uuid import UUID

from quarry.eval.judge import judge_one, make_client
from quarry.eval.metrics import QueryResult, gold_rank, is_gold, summarize
from quarry.infra.db import acquire
from quarry.infra.logging import get_logger
from quarry.infra.settings import Settings
from quarry.pipeline import search_evidence

log = get_logger()

_QUERIES_SQL = """
SELECT qid, query, kind, gold_chunks
FROM eval_queries
WHERE collection_id = $1
ORDER BY kind DESC, qid
"""

_SPANS_SQL = """
SELECT chunk_id, document_id, char_start, char_end
FROM chunks
WHERE collection_id = $1 AND chunk_id = ANY($2::text[]) AND level = 0
"""

_SAVE_RUN_SQL = """
INSERT INTO eval_runs (run_id, label, collection_id, config, metrics)
VALUES ($1, $2, $3, $4, $5)
"""

_SAVE_RESULT_SQL = """
INSERT INTO eval_results (run_id, qid, hit_rank, abstained, evidences,
                          judge, judge_reason, latency_ms, evidence)
VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
"""


async def run_eval(s: Settings, slug: str, collection_id: UUID, label: str, *,
                   top_k: int, use_rerank: bool, use_facts: bool,
                   use_judge: bool) -> tuple[str, dict, list[QueryResult]]:
    async with acquire() as conn:
        rows = await conn.fetch(_QUERIES_SQL, collection_id)
        gold_ids = sorted({g for r in rows for g in r["gold_chunks"]})
        spans = {r["chunk_id"]: (r["document_id"], r["char_start"], r["char_end"])
                 for r in await conn.fetch(_SPANS_SQL, collection_id, gold_ids)}
    if not rows:
        raise ValueError("标注集是空的，先跑 7.2 的导入脚本")
    missing = [g for g in gold_ids if g not in spans]
    if missing:
        log.warning("{} 个金块在库里找不到，语料应该是变过了，重新生成并导入标注集",
                    len(missing))

    client = make_client(s) if use_judge else None       # 配置有问题，在这里就报出来
    items: list[QueryResult] = []
    for n, r in enumerate(rows, 1):
        started = time.perf_counter()
        result = await search_evidence(slug, r["query"], caller_labels=["public"],
                                       top_k=top_k, use_rerank=use_rerank,
                                       use_facts=use_facts)
        latency = (time.perf_counter() - started) * 1000

        gold = [spans[g] for g in r["gold_chunks"] if g in spans]
        fact_status: dict[str, int] = {}
        for e in result.evidences:
            for f in e.facts:
                fact_status[f.status] = fact_status.get(f.status, 0) + 1
        item = QueryResult(
            qid=r["qid"], query=r["query"], kind=r["kind"],
            hit_rank=gold_rank(result.evidences, gold),
            abstained=result.abstain.should_abstain,
            evidences=len(result.evidences),
            latency_ms=round(latency, 1),
            fact_status=fact_status,
            evidence=_snapshot(result, gold),
        )
        if client is not None and r["kind"] == "answerable" and result.evidences:
            try:
                item.judge, item.judge_reason = await judge_one(
                    client, s.judge_model, r["query"],
                    [e.context for e in result.evidences[:3]])
            except Exception as e:
                log.warning("裁判失败 {}：{!r}", r["qid"], e)
        items.append(item)
        print(f"  [{n:>2}/{len(rows)}] {_mark(item)} {item.query[:28]}")

    metrics = summarize(items)
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{label}"
    config = {"label": label, "top_k": top_k, "use_rerank": use_rerank,
              "use_facts": use_facts,
              "rerank_backend": s.rerank_backend if use_rerank else "noop",
              "judge_model": s.judge_model if use_judge else None}
    await _save(run_id, label, collection_id, config, metrics, items)
    return run_id, metrics, items


def _snapshot(result, gold_spans: list) -> list[dict]:
    """给报告留一份证据快照：只留前 3 条，上下文截断，够看清返回了什么就行。"""
    return [{
        "path": " > ".join(e.citation.heading_path),
        "level": e.context_level,
        "gold": is_gold(e, gold_spans),
        "text": e.context[:360],
        "facts": [{"ref": f.ref, "label": f.label, "entity": f.entity,
                   "qualifier": f.qualifier, "value": f.value, "status": f.status,
                   "verified_at": f.verified_at.isoformat() if f.verified_at else None,
                   "written": [w for w in f.written_values if w != f.value]}
                  for f in e.facts],
    } for e in result.evidences[:3]]


def _mark(item: QueryResult) -> str:
    if item.kind == "unanswerable":
        return "✅ 建议拒答" if item.abstained else "❌ 没有拒答"
    rank = f"第 {item.hit_rank} 条" if item.hit_rank else "未命中"
    return (f"{'✅' if item.hit_rank else '❌'} {rank:<6}"
            f"{item.judge or '':<12}{item.latency_ms:>7.0f}ms")


async def _save(run_id: str, label: str, collection_id: UUID, config: dict,
                metrics: dict, items: list[QueryResult]) -> None:
    async with acquire() as conn:
        async with conn.transaction():
            # 第一章给 jsonb 注册过编解码器，这里直接传字典和列表
            await conn.execute(_SAVE_RUN_SQL, run_id, label, collection_id, config, metrics)
            await conn.executemany(_SAVE_RESULT_SQL, [
                (run_id, i.qid, i.hit_rank, i.abstained, i.evidences,
                 i.judge, i.judge_reason, i.latency_ms, i.evidence) for i in items])