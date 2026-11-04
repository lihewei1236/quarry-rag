"""查询时的事实层：把上下文里的易变数值换成占位符，并给出核实过的值。"""
from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from uuid import UUID

from quarry.context.models import FactRef
from quarry.facts.contract import ATTRIBUTES, status_of

_SQL = (Path(__file__).parent / "sql" / "fetch_mentions.sql").read_text(encoding="utf-8")
_PLACEHOLDER = re.compile(r"〔F(\d+) ")


async def fetch_mentions(conn, collection_id: UUID, block_ids: list[str],
                         caller_labels: list[str]) -> list[dict]:
    """取一批父块里的提及，连同匹配到的官方值。"""
    ids = list(dict.fromkeys(b for b in block_ids if b))
    if not ids:
        return []
    return [dict(r) for r in await conn.fetch(_SQL, collection_id, ids, caller_labels)]


class FactMasker:
    """一次查询用一个：mask() 替换数值，finalize() 给最终用到的事实编号。"""

    def __init__(self, rows: list[dict], ttl_days: int, today: date | None = None):
        self._ttl = ttl_days
        self._today = today or date.today()
        self._by_chunk: dict[str, list[dict]] = {}
        for r in rows:                                   # SQL 已按 span_start 排好序
            self._by_chunk.setdefault(r["chunk_id"], []).append(r)
        self._refs: dict[tuple, FactRef] = {}

    def mask(self, chunk_id: str, text: str, offset: int = 0) -> str:
        """text 是父块 chunk_id 的 content[offset : offset+len(text)]，把其中的提及换成占位符。"""
        parts: list[str] = []
        pos = 0
        for r in self._by_chunk.get(chunk_id, []):
            start, end = r["span_start"] - offset, r["span_end"] - offset
            if start < pos or end > len(text):           # 不在这段文字里，或者和前一个重叠
                continue
            ref = self._ref_for(r)
            parts += [text[pos:start], f"〔{ref.ref} {ref.label}〕"]
            pos = end
        return "".join(parts) + text[pos:]

    def finalize(self, contexts: list[str]) -> tuple[list[str], list[list[FactRef]]]:
        """按占位符在最终上下文里第一次出现的顺序重新编号，只留下真正用到的事实。

        预算可能挤掉了一些邻块，里面的占位符不在了，对应的事实也不该交给上游。
        """
        by_old = {ref.ref: ref for ref in self._refs.values()}
        renumber: dict[str, str] = {}
        out_contexts: list[str] = []
        out_facts: list[list[FactRef]] = []
        for ctx in contexts:
            used: list[FactRef] = []

            def swap(m: re.Match) -> str:
                old = f"F{m.group(1)}"
                if old not in by_old:                    # 原文里碰巧有长得像占位符的文字
                    return m.group(0)
                if old not in renumber:
                    renumber[old] = f"F{len(renumber) + 1}"
                if by_old[old] not in used:
                    used.append(by_old[old])
                return f"〔{renumber[old]} "

            out_contexts.append(_PLACEHOLDER.sub(swap, ctx))
            out_facts.append(used)
        for old, new in renumber.items():
            by_old[old].ref = new
        return out_contexts, out_facts

    def _ref_for(self, r: dict) -> FactRef:
        official = r["official_value"] is not None
        # 有官方值：同一实体、属性、季节算一个事实；没有：正文写的值不同就是不同的事实
        key = ((r["official_name"], r["attribute"], r["qualifier"]) if official
               else (r["entity_name"], r["attribute"], r["qualifier"], r["written_value"]))
        ref = self._refs.get(key)
        if ref is None:
            ref = FactRef(
                ref=f"F{len(self._refs) + 1}",
                entity=r["official_name"] if official else r["entity_name"],
                attribute=r["attribute"],
                label=ATTRIBUTES[r["attribute"]],
                qualifier=r["qualifier"],
                value=r["official_value"] if official else r["written_value"],
                status=status_of(r["official_value"], r["verified_at"],
                                 self._ttl, self._today),
                verified_at=r["verified_at"] if official else None,
                source_url=r["source_url"] if official else None,
                written_values=[],
            )
            self._refs[key] = ref
        if r["written_value"] not in ref.written_values:
            ref.written_values.append(r["written_value"])
        return ref


def verified_date(facts: list[FactRef]) -> date | None:
    """来源卡片上的核实日期：取最旧的那个；有未核实的数值就不显示。"""
    if not facts or any(f.verified_at is None for f in facts):
        return None
    return min(f.verified_at for f in facts)