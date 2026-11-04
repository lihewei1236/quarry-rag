"""文档上传与状态查询。"""
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, HTTPException, UploadFile

from quarry.infra.db import acquire
from quarry.ingest.queue import enqueue_document

router = APIRouter(prefix="/v1/documents", tags=["ingest"])

CORPUS_ROOT = Path("corpus")
SUPPORTED = {".md", ".markdown", ".txt", ".docx"}


@router.post("")
async def upload(file: UploadFile, collection_slug: str):
    # 只取文件名，挡住 ../../etc/passwd 这类路径
    name = Path(file.filename or "").name
    if not name or Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(400, f"文件名非法，或格式不支持（支持 {', '.join(sorted(SUPPORTED))}）")

    async with acquire() as conn:
        coll_id = await conn.fetchval(
            "SELECT id FROM collections WHERE slug = $1 AND status = 'active'",
            collection_slug)
        if coll_id is None:
            raise HTTPException(404, f"集合不存在或未激活 {collection_slug}")

        # 存到 corpus/<集合>/ 下：source_uri 的规则和接入脚本一致，worker 才找得到文件
        folder = CORPUS_ROOT / collection_slug
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(await file.read())
        source_uri = f"{collection_slug}/{name}"

        # 版本号规则和接入脚本一致：上一版已 ready 就开新版，没跑完就原地重来
        latest = await conn.fetchrow(
            """SELECT version, status FROM documents
               WHERE collection_id = $1 AND source_uri = $2
               ORDER BY version DESC LIMIT 1""",
            coll_id, source_uri)
        if latest is None:
            version = 1
        elif latest["status"] == "ready":
            version = latest["version"] + 1
        else:
            version = latest["version"]

        doc_id = await conn.fetchval(
            """INSERT INTO documents (collection_id, source_uri, sha256, version, status)
               VALUES ($1, $2, 'pending', $3, 'uploaded')
               ON CONFLICT (collection_id, source_uri, version)
               DO UPDATE SET status = 'uploaded', stage_error = NULL
               RETURNING id""",
            coll_id, source_uri, version)

    await enqueue_document(doc_id, stage="parsing")
    return {"document_id": str(doc_id), "status": "uploaded"}


@router.get("/{doc_id}")
async def status(doc_id: str):
    try:
        uid = UUID(doc_id)
    except ValueError:
        raise HTTPException(404, "文档不存在")
    async with acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status, stage_error, indexed_at FROM documents WHERE id = $1", uid)
    if row is None:
        raise HTTPException(404, "文档不存在")
    return dict(row)