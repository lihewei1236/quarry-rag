"""官方数据：CSV 是唯一来源，每次导入整体替换这个集合的 facts。"""
from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

from quarry.facts.contract import (ATTRIBUTES, QUALIFIERS, norm_entity,
                                   normalize_value, status_of)

COLUMNS = ["entity", "aliases", "attribute", "qualifier", "value",
           "verified_at", "source_url"]


def load_csv(path: Path) -> tuple[list[dict], list[str]]:
    """读取并逐行校验。返回 (合规的行, 错误信息)；有任何错误都不应该导入。"""
    rows: list[dict] = []
    errors: list[str] = []
    owner: dict[str, str] = {}                  # 匹配键 → 实体，用来发现两个实体共用一个名字
    seen: set[tuple[str, str, str]] = set()

    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != COLUMNS:
            return [], [f"表头必须是：{','.join(COLUMNS)}"]
        for line, r in enumerate(reader, start=2):
            cell = {k: (r.get(k) or "").strip() for k in COLUMNS}
            entity, attribute = cell["entity"], cell["attribute"]
            qualifier = cell["qualifier"] or "全年"
            value = normalize_value(attribute, cell["value"])
            try:
                verified_at = date.fromisoformat(cell["verified_at"])
            except ValueError:
                verified_at = None

            problems = [msg for bad, msg in [
                (not entity, "缺实体名"),
                (attribute not in ATTRIBUTES, "attribute 只能是 " + " / ".join(ATTRIBUTES)),
                (qualifier not in QUALIFIERS, "qualifier 只能是 " + " / ".join(QUALIFIERS)),
                (value is None, "value 不合规"),
                (verified_at is None, "verified_at 要写成 YYYY-MM-DD"),
                ((entity, attribute, qualifier) in seen, "同一实体、属性、季节重复出现"),
            ] if bad]
            keys = sorted({norm_entity(x)
                           for x in [entity, *cell["aliases"].split("|")] if x.strip()})
            problems += [f"名称「{k}」同时属于 {owner[k]} 和 {entity}"
                         for k in keys if owner.setdefault(k, entity) != entity]
            if problems:
                errors.append(f"第 {line} 行：" + "；".join(problems))
                continue

            seen.add((entity, attribute, qualifier))
            rows.append({"entity": entity, "match_keys": keys, "attribute": attribute,
                         "qualifier": qualifier, "value": value,
                         "verified_at": verified_at,
                         "source_url": cell["source_url"] or None})
    return rows, errors


async def replace_facts(conn, collection_id: UUID, rows: list[dict]) -> None:
    """整体替换：删掉这个集合的全部官方值再写入。放在一个事务里，失败就原样回滚。"""
    async with conn.transaction():
        await conn.execute("DELETE FROM facts WHERE collection_id = $1", collection_id)
        if rows:
            await conn.executemany(
                """INSERT INTO facts (collection_id, entity_name, match_keys, attribute,
                                      qualifier, value, verified_at, source_url)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
                [(collection_id, r["entity"], r["match_keys"], r["attribute"],
                  r["qualifier"], r["value"], r["verified_at"], r["source_url"])
                 for r in rows])


_COVERAGE_SQL = """
SELECT m.entity_name, m.attribute, m.qualifier, m.value AS written,
       f.entity_name AS official_name, f.value AS official, f.verified_at
FROM fact_mentions m
LEFT JOIN facts f
  ON f.collection_id = m.collection_id
 AND f.match_keys   @> ARRAY[m.entity_norm]
 AND f.attribute     = m.attribute
 AND f.qualifier     = m.qualifier
WHERE m.collection_id = $1
"""


async def coverage(conn, collection_id: UUID, ttl_days: int,
                   today: date | None = None) -> dict:
    """统计正文提及的核验状态，找出正文与官方不一致的提及，以及该重新核实的官方值。"""
    today = today or date.today()
    counts = {"verified": 0, "expired": 0, "unverified": 0}
    conflicts: dict[tuple, int] = {}            # (实体, 属性, 季节, 正文值, 官方值) → 几处
    for r in await conn.fetch(_COVERAGE_SQL, collection_id):
        counts[status_of(r["official"], r["verified_at"], ttl_days, today)] += 1
        if r["official"] is not None and r["official"] != r["written"]:
            key = (r["official_name"], r["attribute"], r["qualifier"],
                   r["written"], r["official"])
            conflicts[key] = conflicts.get(key, 0) + 1
    expired = await conn.fetch(
        """SELECT entity_name, attribute, qualifier, verified_at FROM facts
           WHERE collection_id = $1 AND verified_at < $2
           ORDER BY verified_at""",
        collection_id, today - timedelta(days=ttl_days))
    return {"counts": counts, "conflicts": conflicts, "expired": [dict(r) for r in expired]}