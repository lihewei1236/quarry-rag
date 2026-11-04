"""索引构建。幂等地把 Section 列表写进 chunks 表。

四步：
  1. 切分 + 算 ID：纯计算，不碰数据库；父块和它的子块在这一步就绑在一起
  2. 查库：哪些块已经在本集合里了
  3. 选择性向量化：只给新子块调向量化接口
  4. 一个事务里写完，顺手清掉这份文档名下本轮没再出现的块
"""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

import jieba

from quarry.common.ids import make_chunk_id
from quarry.common.text import normalize
from quarry.common.vectors import to_halfvec_literal
from quarry.infra.db import acquire
from quarry.infra.embed import get_embedder
from quarry.infra.logging import get_logger
from quarry.ingest.parsers.base import Section
from quarry.ingest.splitter import ChildBlock, ParentBlock, split

log = get_logger()

_SQL_DIR = Path(__file__).parent / "sql"
_UPSERT_SQL = (_SQL_DIR / "upsert_chunk.sql").read_text(encoding="utf-8")


async def index_document(
    document_id: UUID,
    collection_id: UUID,
    collection_slug: str,
    source_uri: str,
    doc_version: int,
    acl_labels: list[str],
    sections: list[Section],
    doc_meta: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """索引一份已解析的文档，返回统计信息。"""
    doc_meta = doc_meta if isinstance(doc_meta, dict) else {}
    embedder = get_embedder()

    # ---------- 第一步：切分，父块和子块在这里就配好对，后面不用再猜 ----------
    groups: list[tuple[ParentBlock, str, list[tuple[ChildBlock, str]]]] = []
    for section in sections:
        parent, children = split(section, policy=policy)
        parent_id = make_chunk_id(
            collection_slug, source_uri, parent.heading_path, parent.content)
        child_pairs = [
            (c, make_chunk_id(collection_slug, source_uri,
                              [*c.heading_path, f"#{c.ord}"], c.content))
            for c in children
        ]
        groups.append((parent, parent_id, child_pairs))

    all_ids = [pid for _, pid, _ in groups]
    all_ids += [cid for _, _, pairs in groups for _, cid in pairs]

    # ---------- 第二步：查库，哪些块已经在了（必须带 collection_id） ----------
    async with acquire() as conn:
        rows = await conn.fetch(
            """SELECT chunk_id FROM chunks
               WHERE collection_id = $1 AND chunk_id = ANY($2::text[])""",
            collection_id, all_ids,
        )
    existing = {r["chunk_id"] for r in rows}

    # ---------- 第三步：只给新子块算向量 ----------
    todo = [(c, cid) for _, _, pairs in groups for c, cid in pairs
            if cid not in existing]
    total_children = sum(len(pairs) for _, _, pairs in groups)
    log.info("{}: 子块 {} 个，已存在 {} 个，需要向量化 {} 个",
             source_uri, total_children, total_children - len(todo), len(todo))

    vectors: dict[str, list[float]] = {}
    if todo:
        texts = [_embed_text(c) for c, _ in todo]
        got = await embedder.embed(texts)        # embed.py 内部按每批 10 条切分
        vectors = {cid: v for (_, cid), v in zip(todo, got)}

    # ---------- 第四步：一个事务里写完，顺手清掉孤儿块 ----------
    async with acquire() as conn, conn.transaction():
        for ord_, (parent, parent_id, pairs) in enumerate(groups):
            # 父块：ord 是它在整份文档里的序号；不参与检索，所以没有 tokens 和向量
            await conn.execute(
                _UPSERT_SQL,
                parent_id, document_id, collection_id, doc_version,
                0, None, ord_, parent.heading_path,
                parent.content, parent.char_start, parent.char_end,
                None, None,
                acl_labels, doc_meta,
            )
            for child, child_id in pairs:
                vec = vectors.get(child_id)      # 已存在的子块这里是 None，走更新分支不碰向量
                await conn.execute(
                    _UPSERT_SQL,
                    child_id, document_id, collection_id, doc_version,
                    1, parent_id, child.ord, child.heading_path,
                    child.content, child.char_start, child.char_end,
                    _tsvector_input(child.content),
                    to_halfvec_literal(vec) if vec else None,
                    acl_labels, doc_meta,        # 子块也要带元数据：检索只查子块
                )

        # 先写后删：中途崩了事务整体回滚，不会出现"删了旧的、新的没写进去"
        removed = await _drop_orphans(conn, collection_id, document_id, all_ids)

    return {
        "total_children": total_children,
        "reused": total_children - len(todo),
        "vectorized": len(todo),
        "orphans_removed": removed,
    }


async def _drop_orphans(conn, collection_id, document_id, keep_ids) -> int:
    """删掉这份文档（同一个 document_id）名下、这一轮没有再出现的块。

    它是一道保险：同一个文档行被重新索引、但切出来的块变了时（比如上一次接入
    中途失败后重试，或者以后改成原地重建），多出来的旧块不会永远留在索引里。
    新版本替换旧版本时的清理，由接入脚本的 _retire_old_versions 负责（3.4.6）。
    """
    rows = await conn.fetch(
        """DELETE FROM chunks
           WHERE collection_id = $1
             AND document_id   = $2
             AND NOT (chunk_id = ANY($3::text[]))
           RETURNING chunk_id""",
        collection_id, document_id, keep_ids,
    )
    if rows:
        log.info("清掉孤儿块 {} 个", len(rows))
    return len(rows)


def _embed_text(child: ChildBlock) -> str:
    """算向量用的文本：面包屑拼在正文前面。

    第二章 2.5.3 的规则：面包屑可以拼进"送去算向量的文本"，
    但绝不能拼进 content，因为 char_start / char_end 指向原文。
    """
    crumb = " > ".join(child.heading_path)
    return f"{crumb}\n{child.content}" if crumb else child.content


def _tsvector_input(content: str) -> str:
    """应用层 jieba 分词，组合成空格分隔的字符串。

    ⚠️ 必须先 normalize 再分词。第二章 2.7.2 说过，写入侧和查询侧
    要用同一份归一化规则，否则用户搜全角的"１２０"就匹配不上。
    第四章的查询侧会调用同样的两步。
    """
    return " ".join(jieba.cut(normalize(content)))