"""拒答信号：平台只给建议，要不要拒答由上游决定。"""
from __future__ import annotations

from dataclasses import dataclass

from quarry.retrieval.base import Hit

DEFAULT_CONFIG = {"enabled": True, "min_dense_score": 0.45}


@dataclass
class AbstainSignal:
    should_abstain: bool
    reason: str | None               # "no_hits" 没召回 | "low_score" 分太低 | "no_signal" 没有余弦分
    top_dense_score: float | None    # 召回结果里最高的余弦分
    threshold: float | None
    hit_count: int


def judge(hits: list[Hit], config: dict | None = None) -> AbstainSignal:
    """根据召回结果给出拒答信号。config 来自 collections.abstain_config。"""
    cfg = {**DEFAULT_CONFIG, **(config or {})}
    n = len(hits)
    threshold = float(cfg["min_dense_score"])

    if not cfg["enabled"]:
        return AbstainSignal(False, None, None, None, n)
    if n == 0:
        return AbstainSignal(True, "no_hits", None, threshold, 0)

    # 只看余弦分，原因见 5.6.2
    dense = [h.dense_score for h in hits if h.dense_score is not None]
    if not dense:
        # 结果全部来自全文检索，手里没有能比较的分数：不替上游拦，但标出来
        return AbstainSignal(False, "no_signal", None, threshold, n)

    top = max(dense)
    low = top < threshold
    return AbstainSignal(low, "low_score" if low else None, round(top, 4), threshold, n)