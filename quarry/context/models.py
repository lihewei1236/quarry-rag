"""证据、引用与事实：平台交付给上游的数据形状。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from uuid import UUID


@dataclass
class Citation:
    """一条引用。上游用它渲染来源卡片、跳转原文。"""

    document_id: UUID
    source_uri: str
    title: str | None
    url: str | None                  # 原文链接，来自 documents.meta（第三章的抓取清单写入）
    license: str | None
    heading_path: list[str]
    char_start: int                  # 命中块在原文里的起止位置
    char_end: int
    locator_kind: str                # "char" 精确到字符 | "paragraph" 只到段落 | "unknown"
    verified_at: date | None = None  # 核实日期：证据里最旧的官方核实日期；有未核实的数值时为 None


@dataclass
class FactRef:
    """证据里用到的一个易变数值。上游写答案时，这类数值只能从这里取。"""

    ref: str                         # 编号，对应 context 里的占位符〔F1 …〕
    entity: str
    attribute: str                   # ticket_price | open_hours
    label: str                       # 门票价格 | 开放时间
    qualifier: str                   # 全年 | 旺季 | 淡季
    value: str                       # 交给上游使用的值
    status: str                      # verified 已核实 | expired 核实已过期 | unverified 未核实
    verified_at: date | None         # 官方核实日期；unverified 时为 None
    source_url: str | None
    written_values: list[str]        # 正文里写的值；和 value 不同，说明正文已经过时


@dataclass
class Evidence:
    """一条证据 = 命中块 + 上下文 + 引用 + 分数 + 事实。"""

    chunk_id: str
    text: str                        # 命中块原文，引用区间指向它，用来高亮（不遮蔽）
    context: str                     # 交给大模型读的上下文（开启事实层时已遮蔽）
    context_level: str               # "neighbors" 带邻块 | "section" 本节 | "chunk" 只有命中块
    citation: Citation

    score: float                     # 融合分（第四章）
    rerank_score: float | None
    dense_score: float | None
    sparse_score: float | None
    dense_rank: int | None
    sparse_rank: int | None
    sibling_hits: int                # 同一小节里被合并掉的其他命中数
    facts: list[FactRef] = field(default_factory=list)      # 第六章新增