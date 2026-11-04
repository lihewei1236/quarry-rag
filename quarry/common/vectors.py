"""向量字面量。写入侧和查询侧共用，格式必须一致。"""
from __future__ import annotations


def to_halfvec_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.6g}" for v in vec) + "]"