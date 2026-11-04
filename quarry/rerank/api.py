"""托管重排接口，默认对接百炼 qwen3-rerank。

只要接口的请求是 {model, query, documents, top_n}、
响应是 {results: [{index, relevance_score}]}，换厂商只需要改 .env。
"""
from __future__ import annotations

import asyncio

import httpx

from quarry.infra.logging import get_logger
from quarry.infra.settings import Settings
from quarry.retrieval.base import Hit

log = get_logger()

MAX_DOC_CHARS = 1000        # 单条候选最多送多少字，防止超过接口的单条长度上限


class ApiReranker:
    name = "api"

    def __init__(self, s: Settings):
        # RERANK_API_KEY 留空时复用 LLM_API_KEY：百炼的重排和 embedding 是同一个 key
        key = s.rerank_api_key.get_secret_value() or s.llm_api_key.get_secret_value()
        if not s.rerank_url or not s.rerank_model:
            raise ValueError("RERANK_BACKEND=api 时必须配置 RERANK_URL 和 RERANK_MODEL")
        if not key or "REPLACE-ME" in key:
            raise ValueError("没有可用的 API Key：先填好 LLM_API_KEY，或单独填 RERANK_API_KEY")

        self._url = s.rerank_url
        self._model = s.rerank_model
        self._timeout = s.rerank_timeout_s
        self._client = httpx.AsyncClient(
            timeout=s.rerank_timeout_s,
            headers={"Authorization": f"Bearer {key}"},
        )

    async def rerank(self, query: str, hits: list[Hit], top_k: int) -> list[Hit]:
        # 空文本不送：有的服务端会悄悄丢掉空文档，返回的 index 就对不上了
        usable = [h for h in hits if h.content.strip()]
        if not usable:
            return hits[:top_k]

        body = {
            "model": self._model,
            "query": query,
            "documents": [_doc_text(h) for h in usable],
            "top_n": len(usable),
        }
        try:
            async with asyncio.timeout(self._timeout):
                resp = await self._client.post(self._url, json=body)
            if resp.status_code != 200:
                log.warning("重排接口返回 {}：{}", resp.status_code, resp.text[:300])
                return hits[:top_k]
            scores = _parse(resp.json(), len(usable))
        except Exception as e:                  # 协议第 3 条：不许把异常抛出去
            log.warning("重排请求失败：{!r}", e)
            return hits[:top_k]

        if not scores:
            log.warning("重排接口没有返回可用结果：{}", resp.text[:300])
            return hits[:top_k]

        for idx, score in scores.items():
            usable[idx].rerank_score = score
        ranked = sorted((usable[i] for i in scores),
                        key=lambda h: h.rerank_score, reverse=True)
        return ranked[:top_k]


def _parse(data: dict, n: int) -> dict[int, float]:
    """把响应解析成 {在输入里的位置: 分数}。换一个响应格式不同的厂商，只改这个函数。"""
    out: dict[int, float] = {}
    for item in data.get("results") or []:
        idx, score = item.get("index"), item.get("relevance_score")
        if isinstance(idx, int) and 0 <= idx < n and score is not None:
            out[idx] = float(score)
    return out


def _doc_text(hit: Hit) -> str:
    """送去重排的文本 = 标题路径 + 正文。孤零零一句"门票 120 元"，模型不知道说的是哪个景点。"""
    crumb = " > ".join(hit.heading_path)
    body = hit.content[:MAX_DOC_CHARS]
    return f"{crumb}\n{body}" if crumb else body