"""Parser 协议与 Section 数据结构。

所有解析器实现同一个协议，切分器只认协议不认具体实现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Section:
    """解析器产出的最小单位，通常对应文档里的一个标题小节。"""

    heading_path: list[str]       # 从根标题到当前的完整路径，不含文档标题
    content: str                  # 小节正文，与原文逐字对应
    char_start: int               # 在原文中的起始位置
    char_end: int                 # 在原文中的结束位置
    meta: dict = field(default_factory=dict)


class Parser(Protocol):
    """所有解析器的协议。

    tier 字段会写进 documents.parser_tier，排错时能看出这份文档走了哪一档。
    """

    tier: str        # "A" | "B" | "C"

    def can_parse(self, source_uri: str) -> bool:
        """能不能处理这个来源，按扩展名判断即可。"""
        ...

    def parse(self, path: Path) -> list[Section]:
        """把文档解析成一组 Section。

        三条约定，任何实现都要守住：
        1. Section 顺序与原文一致
        2. 原文[char_start:char_end] 必须严格等于 content
        3. heading_path 从文档标题的下一层开始
        """
        ...