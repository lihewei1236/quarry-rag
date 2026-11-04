"""导出集合的索引快照。

产物：
    snapshots/<集合>/collection.json      集合元信息（模型、维度、切分策略）
    snapshots/<集合>/documents.jsonl.gz   文档表
    snapshots/<集合>/chunks.jsonl.gz      块表（不含向量）
    snapshots/<集合>/manifest.json        以上三个文件的 sha256
    snapshots/<集合>/build-info.json      导出时间，不进清单
"""
import asyncio
import gzip
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from quarry.infra.db import acquire, close_pool, init_pool

MANIFESTED = ("collection.json", "documents.jsonl.gz", "chunks.jsonl.gz")


def as_dict(v) -> dict:
    """JSONB 列可能是 dict / str / None，统一成 dict。"""
    if v is None:
        return {}
    if isinstance(v, str):
        return json.loads(v)
    return dict(v)


def write_jsonl_gz(path: Path, rows) -> None:
    """写一个可复现的 gzip 文件。

    gzip 头部默认会带上当前时间戳，同样的内容两次导出会得到不同的字节，
    校验和也就跟着变。mtime=0 把这个时间戳固定下来。
    """
    with path.open("wb") as raw, \
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        for row in rows:
            gz.write((json.dumps(row, ensure_ascii=False,
                                 sort_keys=True) + "\n").encode("utf-8"))


async def export_collection(coll_slug: str, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    async with acquire() as conn:
        coll = await conn.fetchrow(
            "SELECT * FROM collections WHERE slug = $1 AND status = 'active'",
            coll_slug)
        if coll is None:
            raise ValueError(f"没有 active 集合 {coll_slug}")

        # 这个文件进清单，所以里面不能有导出时间这种每次都变的东西
        (out_dir / "collection.json").write_text(json.dumps({
            "slug": coll["slug"],
            "embed_model": coll["embed_model"],
            "embed_dim": coll["embed_dim"],
            "chunk_policy": as_dict(coll["chunk_policy"]),
        }, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")

        docs = await conn.fetch(
            """SELECT id, source_uri, title, sha256, version, acl_labels, meta
               FROM documents
               WHERE collection_id = $1 AND status = 'ready'
               ORDER BY source_uri, version""", coll["id"])
        write_jsonl_gz(out_dir / "documents.jsonl.gz", [{
            "id": str(d["id"]), "source_uri": d["source_uri"],
            "title": d["title"], "sha256": d["sha256"], "version": d["version"],
            "acl_labels": list(d["acl_labels"]), "meta": as_dict(d["meta"]),
        } for d in docs])

        chunks = await conn.fetch(
            """SELECT chunk_id, document_id, level, parent_id, ord,
                      heading_path, content, char_start, char_end,
                      acl_labels, meta
               FROM chunks WHERE collection_id = $1
               ORDER BY chunk_id""", coll["id"])
        write_jsonl_gz(out_dir / "chunks.jsonl.gz", [{
            "chunk_id": c["chunk_id"], "document_id": str(c["document_id"]),
            "level": c["level"], "parent_id": c["parent_id"], "ord": c["ord"],
            "heading_path": list(c["heading_path"] or []),
            "content": c["content"],
            "char_start": c["char_start"], "char_end": c["char_end"],
            "acl_labels": list(c["acl_labels"]), "meta": as_dict(c["meta"]),
        } for c in chunks])

    manifest = {name: hashlib.sha256((out_dir / name).read_bytes()).hexdigest()
                for name in MANIFESTED}
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    # 导出时间单独放一个文件，不进清单
    (out_dir / "build-info.json").write_text(json.dumps({
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "documents": len(docs),
        "chunks": len(chunks),
    }, indent=2), encoding="utf-8")

    print(f"快照导出到 {out_dir}")
    print(f"  documents {len(docs)}")
    print(f"  chunks    {len(chunks)}")


async def main() -> None:
    slug = sys.argv[1] if len(sys.argv) > 1 else "travel-guide"
    await init_pool()
    try:
        await export_collection(slug, Path("snapshots") / slug)
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())