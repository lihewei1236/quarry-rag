"""评测指标：把每个问题的结果汇总成一次运行的指标。"""
from __future__ import annotations

import math
import unicodedata
from dataclasses import dataclass, field
from statistics import mean
from uuid import UUID

LABELS = {
    "queries": "问题总数",
    "recall": "召回率（金块进前 k）",
    "mrr": "MRR",
    "top1": "金块排第一的比例",
    "false_abstain": "误拒率（有答案却建议拒答）",
    "abstain_correct": "正确拒答率（无答案且建议拒答）",
    "supported": "证据支撑率（裁判判定）",
    "judged": "裁判判过的问题数",
    "p50_ms": "延迟 P50（毫秒）",
    "p95_ms": "延迟 P95（毫秒）",
}


@dataclass
class QueryResult:
    qid: str
    query: str
    kind: str                            # answerable | unanswerable
    hit_rank: int | None                 # 金块排在第几条证据，没命中是 None
    abstained: bool
    evidences: int
    latency_ms: float
    judge: str | None = None             # supported | partial | unsupported
    judge_reason: str | None = None
    fact_status: dict[str, int] = field(default_factory=dict)
    evidence: list[dict] = field(default_factory=list)      # 前 3 条证据的快照，给报告用


def pad(text: str, width: int) -> str:
    """按终端宽度补空格：中文和全角字符算两格，这样打出来的表才是对齐的。"""
    used = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - used)


def is_gold(evidence, gold_spans: list[tuple[UUID, int, int]]) -> bool:
    """这条证据是不是金块里的内容：引用区间完全落在金块区间内就算。"""
    c = evidence.citation
    return any(c.document_id == document_id
               and c.char_start >= start and c.char_end <= end
               for document_id, start, end in gold_spans)


def gold_rank(evidences: list, gold_spans: list[tuple[UUID, int, int]]) -> int | None:
    """金块排在第几条证据里（从 1 数）。都没命中返回 None。"""
    for rank, e in enumerate(evidences, 1):
        if is_gold(e, gold_spans):
            return rank
    return None


# 回归闸门：这三个指标掉得超过阈值就算回退
GATE_LIMITS = (("recall", 0.05, "召回率"),
               ("abstain_correct", 0.10, "正确拒答率"),
               ("supported", 0.10, "证据支撑率"))


def gate_check(base: dict, new: dict) -> list[str]:
    """返回不通过的原因；两次运行都有这个指标才比较。空列表 = 通过。"""
    return [f"{label}从 {base[key]} 掉到 {new[key]}，超过了允许的 {limit}"
            for key, limit, label in GATE_LIMITS
            if base.get(key) is not None and new.get(key) is not None
            and base[key] - new[key] > limit]


def percentile(values: list[float], p: float) -> float:
    """最近秩法：取排序后第 ceil(p% × n) 个值。样本少的时候别过度解读。"""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, math.ceil(p / 100 * len(ordered)) - 1))
    return round(ordered[idx], 1)


def _rate(part: int, whole: int) -> float | None:
    return None if whole == 0 else round(part / whole, 4)


def summarize(items: list[QueryResult]) -> dict:
    answerable = [i for i in items if i.kind == "answerable"]
    negative = [i for i in items if i.kind == "unanswerable"]
    hits = [i for i in answerable if i.hit_rank is not None]
    judged = [i for i in answerable if i.judge]
    facts: dict[str, int] = {}
    for i in items:
        for status, n in i.fact_status.items():
            facts[status] = facts.get(status, 0) + n

    reciprocal = [1 / i.hit_rank for i in hits] + [0.0] * (len(answerable) - len(hits))
    return {
        "queries": len(items),
        "recall": _rate(len(hits), len(answerable)),
        "mrr": round(mean(reciprocal), 4) if reciprocal else None,
        "top1": _rate(len([i for i in hits if i.hit_rank == 1]), len(answerable)),
        "false_abstain": _rate(len([i for i in answerable if i.abstained]), len(answerable)),
        "abstain_correct": _rate(len([i for i in negative if i.abstained]), len(negative)),
        "supported": _rate(len([i for i in judged if i.judge == "supported"]), len(judged)),
        "judged": len(judged),
        "p50_ms": percentile([i.latency_ms for i in items], 50),
        "p95_ms": percentile([i.latency_ms for i in items], 95),
        "facts": facts,
    }


def render(metrics: dict) -> str:
    """把指标打成一列，给终端看。"""
    lines = []
    for key, label in LABELS.items():
        value = metrics.get(key)
        lines.append("  " + pad(label, 32) + ("-" if value is None else str(value)))
    if metrics.get("facts"):
        detail = "，".join(f"{k} {v}" for k, v in sorted(metrics["facts"].items()))
        lines.append("  " + pad("证据里的事实", 32) + detail)
    return "\n".join(lines)