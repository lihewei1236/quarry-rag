"""上下文扩展：同一小节去重、回捞父块和邻块、按预算挑上下文档位。"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from quarry.retrieval.base import Hit

_SQL_DIR = Path(__file__).parent / "sql"
_PARENTS_SQL = (_SQL_DIR / "fetch_parents.sql").read_text(encoding="utf-8")
_NEIGHBORS_SQL = (_SQL_DIR / "fetch_neighbors.sql").read_text(encoding="utf-8")

SEP = "\n\n"


def dedupe_by_parent(hits: list[Hit]) -> list[tuple[Hit, int]]:
    """同一父块只留排在最前面的那条，返回 [(命中, 被合并掉的同节命中数)]。

    输入必须已经按最终顺序排好（重排之后），这样留下的就是最相关的那条。
    """
    first: dict[str, Hit] = {}
    merged: dict[str, int] = {}
    for h in hits:
        key = h.parent_id or h.chunk_id
        if key in first:
            merged[key] += 1
        else:
            first[key] = h
            merged[key] = 0
    return [(h, merged[key]) for key, h in first.items()]


async def fetch_parents(conn, collection_id: UUID, parent_ids: list[str | None],
                        caller_labels: list[str]) -> dict[str, dict]:
    """批量取父块，返回 {父块 chunk_id: 行}。"""
    ids = list(dict.fromkeys(p for p in parent_ids if p))
    if not ids:
        return {}
    rows = await conn.fetch(_PARENTS_SQL, collection_id, ids, caller_labels)
    return {r["chunk_id"]: dict(r) for r in rows}


async def fetch_neighbors(conn, collection_id: UUID, parents: list[dict], radius: int,
                          caller_labels: list[str]) -> dict[str, list[dict]]:
    """批量取每个父块前后 radius 个相邻父块，返回 {父块 chunk_id: [邻块行, ...]}。"""
    if radius <= 0 or not parents:
        return {}
    rows = await conn.fetch(
        _NEIGHBORS_SQL, collection_id,
        [p["document_id"] for p in parents], [p["ord"] for p in parents],
        radius, caller_labels,
    )
    owner = {(p["document_id"], p["ord"]): p["chunk_id"] for p in parents}
    out: dict[str, list[dict]] = {}
    for r in rows:                              # SQL 已经按 ord 排好序
        out.setdefault(owner[(r["document_id"], r["center_ord"])], []).append(dict(r))
    return out


def context_options(parent: dict | None, neighbors: list[dict],
                    chunk_text: str) -> list[tuple[str, str]]:
    """一条证据的上下文，从大到小三档：带邻块 → 只有本节 → 只有命中块。"""
    options: list[tuple[str, str]] = []
    if parent is not None:
        before = [n["content"] for n in neighbors if n["ord"] < parent["ord"]]
        after = [n["content"] for n in neighbors if n["ord"] > parent["ord"]]
        if before or after:
            options.append(("neighbors", SEP.join([*before, parent["content"], *after])))
        options.append(("section", parent["content"]))
    options.append(("chunk", chunk_text))
    return options


def apply_budget(all_options: list[list[tuple[str, str]]],
                 max_chars: int) -> list[tuple[str, str]]:
    """按顺序给每条证据挑一档放得下的上下文；连最小一档都放不下就停。

    第一条证据一定保留。每一档都包含命中块本身，所以降档不会丢掉证据。
    """
    chosen: list[tuple[str, str]] = []
    used = 0
    for i, options in enumerate(all_options):
        remaining = max_chars - used
        pick = next((o for o in options if len(o[1]) <= remaining), None)
        if pick is None:
            if i > 0:
                break
            pick = options[-1]
        chosen.append(pick)
        used += len(pick[1])
    return chosen