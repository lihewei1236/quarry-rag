"""接入 corpus 目录下的所有文档。可以反复跑，幂等的。"""
import asyncio
import hashlib
import json
from pathlib import Path
from uuid import UUID

from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.logging import get_logger
from quarry.ingest.indexer import index_document
from quarry.ingest.parsers import get_parser
from quarry.ingest.splitter import DEFAULT_POLICY

log = get_logger()

CORPUS_ROOT = Path("corpus")
SUPPORTED = {".md", ".markdown", ".txt", ".docx"}

# 目录决定集合，集合决定默认权限标签。第 4 章的权限红线用例依赖这张表。
COLLECTIONS = {
    "travel-guide":   ("旅行攻略", ["public"]),
    "corp-policy":    ("企业制度", ["org:demo-corp"]),
    "internal-notes": ("内部笔记", ["org:demo-corp", "role:manager"]),
}


def load_manifest() -> dict[str, dict]:
    """读 3.1.6 抓取时写的清单，按 source_uri 索引。"""
    out: dict[str, dict] = {}
    manifest_dir = CORPUS_ROOT / "_manifest"
    if not manifest_dir.exists():
        return out
    for f in manifest_dir.glob("*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["source_uri"]] = rec
    return out


async def ensure_collections(conn) -> dict[str, tuple[UUID, dict]]:
    """建租户和三个集合，返回 {集合短名: (集合 ID, 切分策略)}。"""
    tenant_id = await conn.fetchval(
        """INSERT INTO tenants (slug, name) VALUES ('demo', '演示租户')
           ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
           RETURNING id""")

    out: dict[str, tuple[UUID, dict]] = {}
    for slug, (name, labels) in COLLECTIONS.items():
        row = await conn.fetchrow(
            """INSERT INTO collections (tenant_id, slug, name, embed_model,
                                        embed_dim, default_acl_labels, chunk_policy)
               VALUES ($1, $2, $3, 'text-embedding-v4', 1024, $4, $5)
               ON CONFLICT (tenant_id, slug) WHERE status = 'active'
               DO UPDATE SET name = EXCLUDED.name
               RETURNING id, chunk_policy""",
            tenant_id, slug, name, labels, DEFAULT_POLICY)
        out[slug] = (row["id"], row["chunk_policy"] or {})
    return out


async def ingest_file(path: Path, coll_id: UUID, coll_slug: str,
                      acl_labels: list[str], manifest: dict,
                      policy: dict | None = None) -> dict:
    source_uri = path.relative_to(CORPUS_ROOT).as_posix()   # 见 3.4.5
    sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
    parser = get_parser(source_uri)

    async with acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, version, sha256, status FROM documents
               WHERE collection_id = $1 AND source_uri = $2
               ORDER BY version DESC LIMIT 1""",
            coll_id, source_uri)

        if row and row["sha256"] == sha256 and row["status"] == "ready":
            log.info("{}: 内容未变，跳过整条管道", source_uri)
            return {"skipped": True}

        # 上一版已经 ready → 开一个新版本；上一版没跑完（失败、刚上传）→ 就在那一版上重来
        if row is None:
            new_version = 1
        elif row["status"] == "ready":
            new_version = row["version"] + 1
        else:
            new_version = row["version"]

        src = manifest.get(source_uri, {})
        doc_meta = {k: src[k] for k in ("url", "license", "fetched_at") if k in src}

        doc_id = await conn.fetchval(
            """INSERT INTO documents (collection_id, source_uri, sha256, version,
                                      title, status, parser_tier, acl_labels, meta)
               VALUES ($1, $2, $3, $4, $5, 'parsing', $6, $7, $8)
               ON CONFLICT (collection_id, source_uri, version)
               DO UPDATE SET status = 'parsing', stage_error = NULL,
                             sha256 = EXCLUDED.sha256, title = EXCLUDED.title,
                             parser_tier = EXCLUDED.parser_tier,
                             acl_labels = EXCLUDED.acl_labels, meta = EXCLUDED.meta
               RETURNING id""",
            coll_id, source_uri, sha256, new_version,
            src.get("title") or path.stem, parser.tier, acl_labels, doc_meta)

    sections = parser.parse(path)
    stats = await index_document(
        document_id=doc_id,
        collection_id=coll_id,
        collection_slug=coll_slug,
        source_uri=source_uri,
        doc_version=new_version,
        acl_labels=acl_labels,
        sections=sections,
        doc_meta=doc_meta,
        policy=policy,
    )

    async with acquire() as conn, conn.transaction():
        await conn.execute(
            "UPDATE documents SET status = 'ready', indexed_at = now() WHERE id = $1",
            doc_id)
        retired = await _retire_old_versions(conn, coll_id, source_uri, new_version)

    stats["retired_chunks"] = retired
    return stats


async def _retire_old_versions(conn, coll_id, source_uri, keep_version) -> int:
    """新版本就绪后，把旧版本的块删掉，文档行保留用于审计。返回删掉的块数。

    内容没变的块已经在 upsert 时"过继"给新版本了，留在旧版本名下的，
    只剩被改掉或删掉的那部分。不做这一步，它们会和新版本一起被检索到，
    上游会同时拿到去年和今年的门票价格，而且没有任何报错。
    """
    old_ids = [r["id"] for r in await conn.fetch(
        """SELECT id FROM documents
           WHERE collection_id = $1 AND source_uri = $2 AND version < $3""",
        coll_id, source_uri, keep_version)]
    if not old_ids:
        return 0
    rows = await conn.fetch(
        """DELETE FROM chunks
           WHERE collection_id = $1 AND document_id = ANY($2::uuid[])
           RETURNING chunk_id""",
        coll_id, old_ids)
    await conn.execute(
        "UPDATE documents SET status = 'superseded' WHERE id = ANY($1::uuid[])",
        old_ids)
    return len(rows)


async def main() -> None:
    await init_pool()
    try:
        manifest = load_manifest()
        async with acquire() as conn:
            colls = await ensure_collections(conn)

        total = {"files": 0, "skipped": 0, "vectorized": 0,
                 "reused": 0, "orphans": 0, "retired": 0}

        for slug, (coll_id, policy) in colls.items():
            folder = CORPUS_ROOT / slug
            if not folder.exists():
                continue
            _, acl_labels = COLLECTIONS[slug]
            for f in sorted(folder.rglob("*")):
                if not (f.is_file() and f.suffix.lower() in SUPPORTED):
                    continue
                stats = await ingest_file(f, coll_id, slug, acl_labels, manifest, policy)
                total["files"] += 1
                if stats.get("skipped"):
                    total["skipped"] += 1
                else:
                    total["vectorized"] += stats.get("vectorized", 0)
                    total["reused"] += stats.get("reused", 0)
                    total["orphans"] += stats.get("orphans_removed", 0)
                    total["retired"] += stats.get("retired_chunks", 0)

        print()
        print("=" * 50)
        print(f"文件数        {total['files']}")
        print(f"整份跳过      {total['skipped']}")
        print(f"新向量化子块  {total['vectorized']}")
        print(f"复用子块      {total['reused']}")
        print(f"清掉孤儿块    {total['orphans']}")
        print(f"清掉旧版本块  {total['retired']}")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())