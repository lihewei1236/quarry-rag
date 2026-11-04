"""A 档：markdown 直读。"""
from __future__ import annotations

import re
from pathlib import Path

from quarry.ingest.parsers.base import Section

# 匹配标题行：# 一级 / ## 二级 / ### 三级
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*$", re.MULTILINE)


class MarkdownParser:
    """A 档解析器。按标题建树，不做额外处理。"""

    tier = "A"

    def can_parse(self, source_uri: str) -> bool:
        return source_uri.lower().endswith((".md", ".markdown", ".txt"))

    def parse(self, path: Path) -> list[Section]:
        text = path.read_text(encoding="utf-8")

        # (层级, 标题行起点, 标题行终点, 标题文字)
        headings = [
            (len(m.group(1)), m.start(), m.end(), m.group(2).strip())
            for m in _HEADING.finditer(text)
        ]

        if not headings:
            # 整篇没有标题，当成一个小节
            sec = self._make(text, 0, len(text), [])
            return [sec] if sec else []

        drop_title = self._is_document_title(headings)

        sections: list[Section] = []
        stack: list[tuple[int, str]] = []      # (层级, 标题文字)

        for i, (level, h_start, h_end, title) in enumerate(headings):
            # 维护标题栈：遇到同级或更浅的层级就一路弹出
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))

            body_start = h_end
            body_end = headings[i + 1][1] if i + 1 < len(headings) else len(text)

            titles = [t for _, t in stack]
            if drop_title:
                titles = titles[1:]

            sec = self._make(text, body_start, body_end, titles)
            if sec is not None:
                sections.append(sec)

        return sections

    @staticmethod
    def _is_document_title(headings: list[tuple[int, int, int, str]]) -> bool:
        """全文只有一个一级标题，而且它在最前面，才认定它是文档标题。"""
        tops = [h for h in headings if h[0] == 1]
        return len(tops) == 1 and tops[0][1] == headings[0][1]

    @staticmethod
    def _make(text: str, start: int, end: int, titles: list[str]) -> Section | None:
        """把一段区间做成 Section，去掉首尾空白的同时把区间一起收窄。"""
        s, e = start, end
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        if e <= s:
            return None                      # 空小节，不产生 Section
        return Section(
            heading_path=list(titles),
            content=text[s:e],
            char_start=s,
            char_end=e,
        )