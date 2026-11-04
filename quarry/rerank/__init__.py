"""重排工厂：按 RERANK_BACKEND 构造，全进程共用一个。"""
from __future__ import annotations

from quarry.infra.logging import get_logger
from quarry.infra.settings import get_settings
from quarry.rerank.api import ApiReranker
from quarry.rerank.base import Reranker
from quarry.rerank.noop import NoopReranker

log = get_logger()

_reranker: Reranker | None = None


def get_reranker() -> Reranker:
    global _reranker
    if _reranker is None:
        s = get_settings()
        _reranker = ApiReranker(s) if s.rerank_backend == "api" else NoopReranker()
        log.info("重排后端：{}", _reranker.name)
    return _reranker


def reset_reranker() -> None:
    """测试里切换配置、或者每个用例换了事件循环时调用。"""
    global _reranker
    _reranker = None


__all__ = ["Reranker", "get_reranker", "reset_reranker"]