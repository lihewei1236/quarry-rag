"""全量校验：A 档文档的每个块，按 [char_start, char_end] 从原文里取出来，必须和 content 一字不差。"""
from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

from quarry.infra.db import acquire, close_pool, init_pool

CORPUS_ROOT = Path("corpus")


async def load_rows():
    await init_pool()
    try:
        async with acquire() as conn:
            return await conn.fetch(
                """SELECT c.chunk_id, c.content, c.char_start, c.char_end,
                          d.source_uri, d.sha256
                   FROM chunks c
                   JOIN documents d ON d.id = c.document_id
                   WHERE d.status = 'ready' AND d.parser_tier = 'A'""")
    finally:
        await close_pool()


def load_source(uri: str, expected_sha: str) -> str | None:
    """读原文。文件不在、或者改过但没重新接入，返回 None。"""
    path = CORPUS_ROOT / uri
    if not path.exists():
        return None
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        return None
    # 和第三章的解析器保持一致，用 read_text 读（Windows 的换行符会被统一成 \n）
    return path.read_text(encoding="utf-8")


def main() -> None:
    rows = asyncio.run(load_rows())
    sources: dict[str, str | None] = {}
    ok, bad, stale = 0, [], set()

    for r in rows:
        uri = r["source_uri"]
        if uri not in sources:
            sources[uri] = load_source(uri, r["sha256"])
        raw = sources[uri]
        if raw is None:
            stale.add(uri)
            continue
        if raw[r["char_start"]:r["char_end"]] == r["content"]:
            ok += 1
        else:
            bad.append(f"  {uri}  [{r['char_start']}, {r['char_end']}]  {r['chunk_id'][:8]}")

    print(f"逐字相符              {ok}")
    print(f"对不上                {len(bad)}")
    print(f"文件改过、未重新接入  {len(stale)} 份")
    for line in bad[:10]:
        print(line)
    if stale:
        print("有文件改过或已不在：先跑一次 ingest_corpus 再校验。")
    if not bad and not stale:
        print("\n✅ 引用区间全部对得上原文")


if __name__ == "__main__":
    main()