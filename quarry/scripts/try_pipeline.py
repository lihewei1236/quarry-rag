"""跑通检索链路：打印拒答信号、证据、引用、事实和分段耗时。

用法：uv run python -m quarry.scripts.try_pipeline "问题" [集合短名] [--no-facts]
"""
from __future__ import annotations

import asyncio
import sys

from quarry.infra.db import close_pool, init_pool
from quarry.pipeline import search_evidence


def _f(x: float | None, digits: int = 3) -> str:
    return "-" if x is None else f"{x:.{digits}f}"


async def run(query: str, slug: str, use_facts: bool) -> None:
    await init_pool()
    try:
        r = await search_evidence(slug, query, caller_labels=["public"], top_k=3,
                                  use_facts=use_facts)
    finally:
        await close_pool()

    a = r.abstain
    print(f"\n查询      {r.query}")
    print(f"拒答建议  {a.should_abstain}（原因 {a.reason}，"
          f"最高余弦分 {_f(a.top_dense_score, 4)}，阈值 {a.threshold}）")
    print(f"重排      {r.debug['rerank_backend']}，真的重排过：{r.debug['reranked']}")
    print(f"事实层    {'开' if r.debug['facts_enabled'] else '关'}")
    print(f"候选      {r.debug['candidates']} 条，上下文共 {r.debug['context_chars']} 字\n")

    for i, e in enumerate(r.evidences, 1):
        c = e.citation
        print(f"[{i}] 融合 {e.score:.4f}  余弦 {_f(e.dense_score)}  重排 {_f(e.rerank_score)}")
        print(f"    来源    {c.source_uri}  {' > '.join(c.heading_path)}")
        print(f"    区间    [{c.char_start}, {c.char_end}]  定位粒度 {c.locator_kind}"
              f"  核实日期 {c.verified_at}")
        print(f"    上下文  {e.context_level}，{len(e.context)} 字；同节合并 {e.sibling_hits} 条")
        for fact in e.facts:
            when = f"核实于 {fact.verified_at}" if fact.verified_at else "未核实"
            old = [w for w in fact.written_values if w != fact.value]
            note = f"（正文写的是 {'、'.join(old)}）" if old else ""
            print(f"    事实    {fact.ref} {fact.entity} {fact.label}·{fact.qualifier} = "
                  f"{fact.value}  {fact.status}，{when}{note}")
        mark = e.context.find("〔F")
        if mark >= 0:
            snippet = e.context[max(0, mark - 20):mark + 40].replace("\n", " ")
            print(f"    遮蔽后  …{snippet}…")
        preview = e.text[:40].replace("\n", " ")
        print(f"    正文    {preview}...\n")

    print("耗时（毫秒）")
    for k, v in r.timings.items():
        print(f"    {k:<12}{v}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--no-facts"]
    question = args[0] if args else "兵马俑的门票多少钱"
    collection = args[1] if len(args) > 1 else "travel-guide"
    asyncio.run(run(question, collection, use_facts="--no-facts" not in sys.argv))