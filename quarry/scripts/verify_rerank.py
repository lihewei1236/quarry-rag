"""重排冒烟测试：三段文本里只有一段相关，而且故意不放在第一位。

通过条件：真的重排过，并且相关的那段被排到了第一。
"""
from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

from quarry.infra.settings import get_settings
from quarry.rerank import get_reranker
from quarry.retrieval.base import Hit

QUERY = "兵马俑博物馆的门票多少钱"
DOCS = [
    (["科技"], "量子计算是计算科学的一个前沿领域。"),
    (["西安", "兵马俑"], "兵马俑博物馆门票 120 元，开放时间 8:30 至 18:00。"),
    (["西安", "美食"], "回民街人均消费约 60 元，晚上八点后最热闹。"),
]


def make_hit(i: int, path: list[str], text: str) -> Hit:
    return Hit(
        chunk_id=f"smoke-{i}", document_id=uuid4(), parent_id=None,
        heading_path=path, content=text, char_start=0, char_end=len(text),
        score=0.0, dense_rank=i + 1, sparse_rank=None,
        dense_score=None, sparse_score=None,
    )


async def main() -> int:
    if get_settings().rerank_backend != "api":
        print("RERANK_BACKEND 不是 api，先按 5.2.3 改好 .env")
        return 1

    reranker = get_reranker()                  # 缺配置会在这里直接报错
    hits = [make_hit(i, path, text) for i, (path, text) in enumerate(DOCS)]
    ranked = await reranker.rerank(QUERY, hits, top_k=3)

    for h in ranked:
        score = "None  " if h.rerank_score is None else f"{h.rerank_score:.4f}"
        print(f"  {score}  {h.content}")

    if not ranked or ranked[0].rerank_score is None:
        print("❌ 没有真正重排：接口调用失败，已按原顺序返回。往上看日志里的 warning。")
        return 1
    if "兵马俑" not in ranked[0].content:
        print("❌ 接口通了，但相关的那段没有排第一。检查 RERANK_MODEL 是不是重排模型。")
        return 1
    print("✅ 重排接口可用")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))