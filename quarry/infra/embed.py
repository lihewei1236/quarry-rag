"""向量化客户端。走 OpenAI 兼容接口，换供应商只改 .env。"""
from __future__ import annotations

import hashlib
import math
from typing import Protocol

from openai import AsyncOpenAI

from quarry.infra.logging import get_logger
from quarry.infra.settings import Settings, get_settings

log = get_logger()

# 单次请求最多送几条文本。百炼 text-embedding-v4 的上限是 10 条，超过会直接 400。
# 换成 OpenAI 等上限更高的厂商时可以调大。
_BATCH = 10


class Embedder(Protocol):
    name: str

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


class ApiEmbedder:
    """真实调用。"""

    name = "api"

    def __init__(self, s: Settings):
        self._client = AsyncOpenAI(
            api_key=s.llm_api_key.get_secret_value(),
            base_url=s.llm_base_url,
            timeout=30.0,
        )
        self._model = s.model_embed
        self._dim = s.embed_dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), _BATCH):
            batch = texts[i:i + _BATCH]
            resp = await self._client.embeddings.create(
                model=self._model,
                input=batch,
                dimensions=self._dim,       # 显式指定维度，不依赖模型默认值
            )
            # 服务端不保证按输入顺序返回，按 index 排回去
            for item in sorted(resp.data, key=lambda d: d.index):
                if len(item.embedding) != self._dim:
                    raise ValueError(
                        f"模型 {self._model} 返回 {len(item.embedding)} 维，"
                        f"配置要求 {self._dim} 维。"
                    )
                out.append(item.embedding)
        return out


class FakeEmbedder:
    """把文本哈希成一个固定维度的单位向量。不需要 API Key。"""

    name = "fake"

    def __init__(self, s: Settings):
        self._dim = s.embed_dim
        log.warning("EMBED_BACKEND=fake：向量是哈希出来的，检索结果没有语义意义。")

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        raw = b""
        seed = text.encode("utf-8")
        while len(raw) < self._dim * 2:
            seed = hashlib.sha256(seed).digest()
            raw += seed
        vals = [(raw[2 * i] << 8 | raw[2 * i + 1]) / 32767.5 - 1.0
                for i in range(self._dim)]
        norm = math.sqrt(sum(v * v for v in vals)) or 1.0
        return [v / norm for v in vals]


_embedder: Embedder | None = None


def get_embedder() -> Embedder:
    """全进程单例。"""
    global _embedder
    if _embedder is None:
        s = get_settings()
        _embedder = FakeEmbedder(s) if s.embed_backend == "fake" else ApiEmbedder(s)
    return _embedder