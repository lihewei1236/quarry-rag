"""结构感知切分。一个 Section 变成一个父块加若干子块。

三步：
  _atomic_spans()  找出不能切开的区域：围栏代码块、连续的表格行
  _segment()       把正文切成片段序列：原子区域整块保留，其余按空行分段
  _pack()          把片段打包成子块：普通段落尽量合并到 max_child_size 字，
                   原子片段单独成块，单个超长段落在句末标点处切开

父块原样保留整个小节，不参与检索，只在子块命中后被回捞出来当上下文。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from quarry.ingest.parsers.base import Section

_PARA_SEP = re.compile(r"\n[ \t]*\n")            # 段落分隔：空行
_SENT_END = re.compile(r"[。！？；!?;\n]")        # 强制切分时找的句末位置

DEFAULT_MAX_CHILD = 400          # 子块目标字数
DEFAULT_HARD_MAX = 800           # 硬上限，超过必须切

# 切分策略快照，建集合时写进 collections.chunk_policy（见 3.3.5）
DEFAULT_POLICY = {
    "max_child_size": DEFAULT_MAX_CHILD,
    "hard_max": DEFAULT_HARD_MAX,
    "atomic_kinds": ["table", "code"],
    "version": "v1",
}


@dataclass
class ParentBlock:
    heading_path: list[str]
    content: str
    char_start: int
    char_end: int


@dataclass
class ChildBlock:
    heading_path: list[str]
    content: str
    char_start: int
    char_end: int
    ord: int                     # 在父块内的顺序，从 0 开始


def split(
    section: Section, policy: dict | None = None,
) -> tuple[ParentBlock, list[ChildBlock]]:
    """把一个 Section 切成一个父块和一串子块。

    保证三件事：
    1. 子块按原文顺序排列，ord 从 0 递增
    2. 每个子块的 char 区间落在父块区间之内
    3. 原文[char_start:char_end] 严格等于 content
    """
    policy = policy or {}
    max_size = int(policy.get("max_child_size", DEFAULT_MAX_CHILD))
    hard_max = int(policy.get("hard_max", DEFAULT_HARD_MAX))

    text = section.content
    base = section.char_start      # 小节在原文里的起点，子块区间要加上它

    parent = ParentBlock(
        heading_path=list(section.heading_path),
        content=text,
        char_start=section.char_start,
        char_end=section.char_end,
    )

    pieces = _segment(text)
    packed = _pack(text, pieces, max_size, hard_max)

    children = [
        ChildBlock(
            heading_path=list(section.heading_path),
            content=text[s:e],               # 直接从原文取，内容和区间天然一致
            char_start=base + s,             # 相对位置 + 小节起点 = 原文里的位置
            char_end=base + e,
            ord=i,
        )
        for i, (s, e) in enumerate(packed)
    ]
    return parent, children


# ----------------------------------------------------------------------
# 第一步：找出不能被切开的原子区域

def _atomic_spans(text: str) -> list[tuple[int, int]]:
    """逐行扫描，找出围栏代码块与连续表格行的区间。"""
    spans: list[tuple[int, int]] = []
    offset = 0                # 当前行在 text 里的起点
    in_code = False
    code_start = 0
    table_start = -1          # -1 表示当前不在表格里

    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("```"):
            if in_code:
                spans.append((code_start, offset + len(line)))   # 代码块结束
                in_code = False
            else:
                if table_start >= 0:                              # 先关掉没关的表格
                    spans.append((table_start, offset))
                    table_start = -1
                in_code, code_start = True, offset               # 代码块开始
        elif not in_code:
            is_table = stripped.startswith("|") and stripped.endswith("|")
            if is_table and table_start < 0:
                table_start = offset
            elif not is_table and table_start >= 0:
                spans.append((table_start, offset))
                table_start = -1
        offset += len(line)

    # 文件结尾还没关上的区域
    if in_code:
        spans.append((code_start, offset))
    if table_start >= 0:
        spans.append((table_start, offset))
    return spans


# ----------------------------------------------------------------------
# 第二步：把正文切成片段序列，标注哪些是原子的

def _segment(text: str) -> list[tuple[int, int, bool]]:
    """返回 [(起, 止, 是否原子), ...]，按原文顺序排列。"""
    pieces: list[tuple[int, int, bool]] = []
    cursor = 0
    for a_start, a_end in _atomic_spans(text):
        if a_start > cursor:
            pieces.extend(_paragraphs(text, cursor, a_start))
        s, e = _trim(text, a_start, a_end)
        if e > s:
            pieces.append((s, e, True))
        cursor = a_end
    if cursor < len(text):
        pieces.extend(_paragraphs(text, cursor, len(text)))
    if not pieces:
        s, e = _trim(text, 0, len(text))
        if e > s:
            pieces.append((s, e, False))
    return pieces


def _paragraphs(text: str, start: int, end: int) -> list[tuple[int, int, bool]]:
    """在 [start, end) 里按空行切段。"""
    out: list[tuple[int, int, bool]] = []
    cursor = start
    for m in _PARA_SEP.finditer(text, start, end):
        s, e = _trim(text, cursor, m.start())
        if e > s:
            out.append((s, e, False))
        cursor = m.end()
    s, e = _trim(text, cursor, end)
    if e > s:
        out.append((s, e, False))
    return out


def _trim(text: str, start: int, end: int) -> tuple[int, int]:
    """去掉首尾空白，同时把两个端点一起收窄。"""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


# ----------------------------------------------------------------------
# 第三步：打包

def _pack(
    text: str, pieces: list[tuple[int, int, bool]], max_size: int, hard_max: int,
) -> list[tuple[int, int]]:
    packed: list[tuple[int, int]] = []
    buf_start = buf_end = -1         # 正在攒的子块区间，-1 表示空

    def flush() -> None:
        nonlocal buf_start
        if buf_start >= 0:
            packed.append((buf_start, buf_end))
            buf_start = -1

    for p_start, p_end, atomic in pieces:
        if atomic:
            flush()                                  # 原子片段单独成块，不和别人合并
            packed.append((p_start, p_end))
            continue
        for s, e in _hard_split(text, p_start, p_end, hard_max):
            if buf_start < 0:
                buf_start, buf_end = s, e
            elif e - buf_start <= max_size:
                buf_end = e                          # 合并；中间的空行也留在 content 里
            else:
                flush()
                buf_start, buf_end = s, e
    flush()
    return packed


def _hard_split(
    text: str, start: int, end: int, hard_max: int,
) -> list[tuple[int, int]]:
    """一个段落自己就超过硬上限时，在窗口内最后一个句末标点处切开。"""
    if end - start <= hard_max:
        return [(start, end)]

    out: list[tuple[int, int]] = []
    cursor = start
    while end - cursor > hard_max:
        window_end = cursor + hard_max
        cut = -1
        for m in _SENT_END.finditer(text, cursor, window_end):
            cut = m.end()                            # 取窗口内最后一个句末
        if cut <= cursor:
            cut = window_end                         # 一个标点都没有，只能硬切
        s, e = _trim(text, cursor, cut)
        if e > s:
            out.append((s, e))
        cursor = cut
    s, e = _trim(text, cursor, end)
    if e > s:
        out.append((s, e))
    return out