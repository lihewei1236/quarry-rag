"""查询侧的文本处理。写入侧在 quarry/ingest/indexer.py，两边共用 normalize。"""
from __future__ import annotations

import jieba

from quarry.common.text import normalize

# 这些词在 'simple' 配置下不会被数据库过滤掉，只能我们自己挑掉。
# 留着的话，一个"的"字能匹配几乎所有块，把 ts_rank_cd 的分数彻底拉平。
_STOP = {
    "的", "了", "吗", "呢", "啊", "吧", "是", "在", "和", "与", "或",
    "有", "我", "你", "他", "它", "这", "那", "什么", "多少", "怎么",
    "怎样", "如何", "可以", "能不能", "需要", "一个", "一下", "请问",
    "大概", "左右", "以及", "还有", "然后", "就是",
}

# to_tsquery('simple', '') 会直接报语法错误，
# 所以查询词被过滤光时给一个永远匹配不上的词。
_NO_MATCH = "'__quarry_no_match__'"


def tokenize_for_index(text: str) -> str:
    """写入侧用。空格分隔的词串，交给 to_tsvector。"""
    return " ".join(jieba.cut(normalize(text)))


def build_tsquery(query: str) -> str:
    """查询侧用。拼成 to_tsquery 能吃的 OR 表达式。

    输入  兵马俑的门票多少钱
    输出  '兵马俑' | '门票' | '钱'
    """
    terms: list[str] = []
    for raw in jieba.cut(normalize(query)):
        t = raw.strip()
        if not t or t in _STOP:
            continue
        if not any(ch.isalnum() for ch in t):      # 纯标点，丢掉
            continue
        terms.append("'" + t.replace("'", "''") + "'")

    # dict.fromkeys 去重的同时保住顺序
    uniq = list(dict.fromkeys(terms))
    return " | ".join(uniq) if uniq else _NO_MATCH