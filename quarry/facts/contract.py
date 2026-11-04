"""事实层的契约：抽哪几种数值、值写成什么格式、怎么校验、状态怎么算。

模型的输出一律当作"待校验的建议"：不合契约的条目直接丢弃，宁缺毋滥。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import date

ATTRIBUTES = {"ticket_price": "门票价格", "open_hours": "开放时间"}
QUALIFIERS = ("全年", "旺季", "淡季")
MAX_QUOTE_CHARS = 30

_PRICE = re.compile(r"^\d{1,5}(\.\d{1,2})?$")
_HOURS = re.compile(r"^([01]\d|2[0-3]):[0-5]\d-([01]\d|2[0-4]):[0-5]\d$")


def norm_entity(name: str) -> str:
    """实体名规范化：全角转半角、去掉空白、英文转小写。只用来匹配，不用来展示。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", name)).lower()


def normalize_value(attribute: str, value: object) -> str | None:
    """校验并规范化一个值：价格只留数字，时间统一成 HH:MM-HH:MM。不合规返回 None。"""
    v = unicodedata.normalize("NFKC", str(value)).strip()
    if attribute == "ticket_price":
        if v == "免费":
            return "0"
        v = v.lstrip("¥").removesuffix("元").strip()
        if not _PRICE.match(v) or float(v) > 10000:
            return None
        return str(int(float(v))) if float(v).is_integer() else v
    if attribute == "open_hours":
        v = re.sub(r"\s*[-–—~至到]\s*", "-", v)
        v = re.sub(r"(?<!\d)(\d):", r"0\1:", v)            # 8:30 → 08:30
        if not _HOURS.match(v) or v[:5] >= v[6:]:
            return None
        return v
    return None


def check_item(item: object) -> tuple[dict | None, str | None]:
    """检查模型给出的一条结果。返回 (规范化后的条目, None) 或 (None, 丢弃原因)。"""
    if not isinstance(item, dict):
        return None, "格式不对"
    attribute = item.get("attribute")
    if attribute not in ATTRIBUTES:
        return None, "属性不在清单里"
    qualifier = item.get("qualifier") or "全年"
    if qualifier not in QUALIFIERS:
        return None, "季节标注不合规"
    entity = str(item.get("entity") or "").strip()
    if not entity:
        return None, "缺实体名"
    quote = str(item.get("quote") or "")
    # 原文片段必须短、必须带数字：防止模型把整句话当片段，遮蔽时误伤一大段
    if not quote or len(quote) > MAX_QUOTE_CHARS or not re.search(r"\d|免费", quote):
        return None, "原文片段不合规"
    value = normalize_value(attribute, item.get("value", ""))
    if value is None:
        return None, "值不合规"
    return {"entity": entity, "attribute": attribute, "qualifier": qualifier,
            "value": value, "quote": quote}, None


def status_of(official_value: str | None, verified_at: date | None,
              ttl_days: int, today: date) -> str:
    """没有官方值 → unverified；官方核实日期在有效期内 → verified；否则 → expired。"""
    if official_value is None or verified_at is None:
        return "unverified"
    return "verified" if (today - verified_at).days <= ttl_days else "expired"