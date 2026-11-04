"""标注集：问题 ID 的算法，以及 CSV 的读取与校验。"""
from __future__ import annotations

import csv
import hashlib
from pathlib import Path

COLUMNS = ["query", "kind", "gold_chunks", "note"]
KINDS = ("answerable", "unanswerable")


def qid_of(query: str) -> str:
    """问题 ID 由问题内容算出：同一个问题反复导入，ID 不变，历史结果还能对得上。"""
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()[:16]


def load_csv(path: Path) -> tuple[list[dict], list[str]]:
    """读取并逐行校验。返回 (合规的行, 错误信息)；有任何错误都不应该导入。"""
    rows: list[dict] = []
    errors: list[str] = []
    seen: dict[str, int] = {}

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            return [], [f"表头必须是：{','.join(COLUMNS)}"]
        for line, r in enumerate(reader, start=2):
            cell = {k: (r.get(k) or "").strip() for k in COLUMNS}
            query, kind = cell["query"], cell["kind"]
            gold = [g.strip() for g in cell["gold_chunks"].split("|") if g.strip()]

            problems = [msg for bad, msg in [
                (len(query) < 4, "问题太短"),
                (kind not in KINDS, "kind 只能是 " + " / ".join(KINDS)),
                (kind == "answerable" and not gold, "有答案的问题必须写 gold_chunks"),
                (kind == "unanswerable" and bool(gold), "无答案的问题不能写 gold_chunks"),
                (query in seen, f"和第 {seen.get(query)} 行的问题重复"),
            ] if bad]
            if problems:
                errors.append(f"第 {line} 行：" + "；".join(problems))
                continue

            seen[query] = line
            rows.append({"qid": qid_of(query), "query": query, "kind": kind,
                         "gold_chunks": gold, "note": cell["note"] or None})
    return rows, errors