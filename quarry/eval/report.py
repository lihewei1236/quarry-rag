"""把两次评测运行渲染成一个单文件 HTML 报告：数据内联，不依赖服务和网络。"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from uuid import UUID

from quarry.eval.metrics import LABELS, gate_check

TEMPLATE = Path(__file__).parent / "report.html"

_RUN_SQL = """
SELECT run_id, label, config, metrics
FROM eval_runs
WHERE collection_id = $1 AND (run_id = $2 OR label = $2)
ORDER BY created_at DESC
LIMIT 1
"""

_RESULTS_SQL = """
SELECT r.qid, r.hit_rank, r.abstained, r.judge, r.judge_reason, r.latency_ms, r.evidence,
       q.query, q.kind, q.note
FROM eval_results r
JOIN eval_queries q ON q.qid = r.qid AND q.collection_id = $2
WHERE r.run_id = $1
"""


def _conf(config: dict) -> str:
    return (f"rerank={config.get('use_rerank')} facts={config.get('use_facts')} "
            f"top_k={config.get('top_k')} judge={config.get('judge_model') or '-'}")


def _side(row: dict) -> dict:
    return {"rank": row["hit_rank"], "ms": round(row["latency_ms"]),
            "abstained": row["abstained"], "judge": row["judge"],
            "judge_reason": row["judge_reason"], "evidence": row["evidence"] or []}


def _direction(kind: str, base_rank: int | None, new_rank: int | None) -> str:
    if kind != "answerable":
        return "neg"
    if base_rank == new_rank:
        return "same"
    if base_rank is None:
        return "up"
    if new_rank is None:
        return "down"
    return "up" if new_rank < base_rank else "down"


def _metric_rows(base: dict, new: dict) -> list[dict]:
    rows = []
    for key, name in LABELS.items():
        if key in ("queries", "judged"):
            continue
        b, n = base.get(key), new.get(key)
        is_ms = key.endswith("_ms")
        lower_better = is_ms or key == "false_abstain"
        delta, direction = "—", ""
        if b is not None and n is not None:
            d = n - b
            direction = "" if abs(d) < 1e-9 else ("up" if (d < 0) == lower_better else "down")
            delta = f"{d:+.0f}" if is_ms else f"{d:+.3f}"
        width = lambda v: 0 if v is None else min(100, v / 8 if is_ms else v * 100)
        rows.append({"name": name,
                     "base": "—" if b is None else (f"{b:.0f}" if is_ms else f"{b:.3f}"),
                     "new": "—" if n is None else (f"{n:.0f}" if is_ms else f"{n:.3f}"),
                     "delta": delta, "dir": direction,
                     "bar_base": width(b), "bar_new": width(n)})
    return rows


async def build(conn, collection_id: UUID, slug: str,
                base_key: str, new_key: str) -> dict:
    """把两次运行的记录组装成报告数据。找不到运行记录时抛 ValueError。"""
    runs = {}
    for side, key in (("base", base_key), ("new", new_key)):
        row = await conn.fetchrow(_RUN_SQL, collection_id, key)
        if row is None:
            raise ValueError(f"找不到运行记录：{key}")
        runs[side] = row

    results = {side: {r["qid"]: r for r in
                      await conn.fetch(_RESULTS_SQL, runs[side]["run_id"], collection_id)}
               for side in ("base", "new")}

    items = []
    for qid, new_row in results["new"].items():
        base_row = results["base"].get(qid)
        if base_row is None:                     # 标注集换过，这道题基线里没有，跳过
            continue
        items.append({"qid": qid, "query": new_row["query"], "kind": new_row["kind"],
                      "note": new_row["note"],
                      "dir": _direction(new_row["kind"], base_row["hit_rank"],
                                        new_row["hit_rank"]),
                      "base": _side(base_row), "new": _side(new_row)})
    items.sort(key=lambda i: ({"down": 0, "up": 1, "same": 2, "neg": 3}[i["dir"]],
                              i["query"]))

    base_m, new_m = runs["base"]["metrics"], runs["new"]["metrics"]
    better = len([i for i in items if i["dir"] == "up"])
    worse = len([i for i in items if i["dir"] == "down"])
    problems = gate_check(base_m, new_m)
    headline = (f"{len(items)} 题里，<b class=\"up\">{better} 题变好</b>、"
                f"<b class=\"down\">{worse} 题变坏</b>；召回率 {base_m.get('recall')} → "
                f"<b>{new_m.get('recall')}</b>，P95 {base_m.get('p95_ms')} → "
                f"<b>{new_m.get('p95_ms')}</b> 毫秒。")
    return {
        "collection": slug,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "top_k": runs["new"]["config"].get("top_k", 5),
        "base": {"run_id": runs["base"]["run_id"], "label": runs["base"]["label"],
                 "config_text": _conf(runs["base"]["config"])},
        "new": {"run_id": runs["new"]["run_id"], "label": runs["new"]["label"],
                "config_text": _conf(runs["new"]["config"])},
        "headline": headline,
        "metrics": _metric_rows(base_m, new_m),
        "gate": {"ok": not problems, "problems": problems},
        "items": items,
    }


def render(data: dict, out_path: Path) -> Path:
    """把数据内联进模板，写出单个 HTML 文件。"""
    # 把 </ 转义，防止证据原文里的结束标签把数据块提前闭合
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        TEMPLATE.read_text(encoding="utf-8").replace("__DATA__", payload),
        encoding="utf-8")
    return out_path