"""第一次跑通：插入几段文本 + 一条向量检索。

不做切分、不做融合、不做重排，只让你看到一条带分数的检索结果。
这里的实现方式（手工切分、简化父子关系、内嵌的 make_chunk_id）只是为了本章跑通，
不是本项目的正确写法。第 2 章会把块标识抽成正式模块，第 3 章会重新做接入。
"""
import asyncio
import hashlib

import jieba
from quarry.common.ids import make_chunk_id
from quarry.infra.db import acquire, close_pool, init_pool
from quarry.infra.embed import get_embedder
from quarry.infra.logging import get_logger

log = get_logger()

# 一份最小的示例文档，故意选跟旅行相关的内容
SAMPLE_DOC = {
    "source_uri": "hello_sample.md",
    "sections": [
        (["西安旅游攻略", "必游景点", "兵马俑博物馆"],
         "兵马俑博物馆门票 120 元,开放时间 8:30 至 18:00。"
         "建议从北门进入,先看一号坑再看三号坑,全程步行约两小时。"),
        (["西安旅游攻略", "必游景点", "大雁塔"],
         "大雁塔位于大慈恩寺内,进寺门票 50 元,登塔另收 30 元。"
         "晚间北广场有音乐喷泉表演。"),
        (["西安旅游攻略", "必游景点", "华清宫"],
         "华清宫门票 120 元,包含骊山与华清池两个景区。"
         "冬季 9:00 开门,夏季提前到 8:30。"),
        (["西安旅游攻略", "美食推荐", "回民街"],
         "回民街必尝:贾三灌汤包、老米家泡馍、盛家酿皮。"
         "人均消费约 60 元,晚上八点后最热闹。"),
    ],
}




def to_halfvec_literal(vec: list[float]) -> str:
    """把 Python 列表写成 pgvector 认识的字面量，例如 [0.1,0.2,...]。"""
    return "[" + ",".join(f"{v:.6g}" for v in vec) + "]"


async def seed():
    """先建一个租户 + 一个集合 + 一份文档，再往 chunks 里灌数据。"""
    embedder = get_embedder()
    async with acquire() as conn:
        # 建租户
        tenant_id = await conn.fetchval(
            """INSERT INTO tenants (slug, name) VALUES ($1, $2)
               ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
               RETURNING id""",
            "demo", "演示租户")

        # 建集合
        coll_id = await conn.fetchval(
            """INSERT INTO collections (tenant_id, slug, name, embed_model, embed_dim)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (tenant_id, slug) WHERE status = 'active'
               DO UPDATE SET name = EXCLUDED.name
               RETURNING id""",
            tenant_id, "hello", "Hello 集合", "text-embedding-v4", 1024)

        # 建文档
        content_hash = hashlib.sha256(
            SAMPLE_DOC["source_uri"].encode()).hexdigest()
        doc_id = await conn.fetchval(
            """INSERT INTO documents (collection_id, source_uri, sha256, status,
                                       acl_labels)
               VALUES ($1, $2, $3, 'ready', $4)
               ON CONFLICT (collection_id, source_uri, version)
               DO UPDATE SET status = 'ready'
               RETURNING id""",
            coll_id, SAMPLE_DOC["source_uri"], content_hash, ["public"])

        # 向量化
        texts = [content for _, content in SAMPLE_DOC["sections"]]
        vectors = await embedder.embed(texts)

        # 清一遍旧数据，让脚本可以重跑
        await conn.execute("DELETE FROM chunks WHERE document_id = $1", doc_id)

        for ord_, ((path, content), vec) in enumerate(
                zip(SAMPLE_DOC["sections"], vectors)):
            # 本章简化：一个小节 = 一个父块 + 一个子块
            parent_id = make_chunk_id("hello", SAMPLE_DOC["source_uri"], path, content)
            child_id = make_chunk_id("hello", SAMPLE_DOC["source_uri"], [*path, "#0"], content)
            # jieba 分词写 tsvector
            tokens_text = " ".join(jieba.cut(content))

            # 父块（level=0，无 embedding）
            await conn.execute(
                """INSERT INTO chunks (chunk_id, document_id, collection_id,
                    doc_version, level, parent_id, ord, heading_path, content,
                    char_start, char_end, acl_labels)
                   VALUES ($1, $2, $3, 1, 0, NULL, $4, $5, $6, 0, $7, $8)""",
                parent_id, doc_id, coll_id, ord_, path, content,
                len(content), ["public"])

            # 子块（level=1，有 embedding）
            await conn.execute(
                """INSERT INTO chunks (chunk_id, document_id, collection_id,
                    doc_version, level, parent_id, ord, heading_path, content,
                    char_start, char_end, tokens, embedding, acl_labels)
                   VALUES ($1, $2, $3, 1, 1, $4, 0, $5, $6, 0, $7,
                           to_tsvector('simple', $8), $9::halfvec(1024), $10)""",
                child_id, doc_id, coll_id, parent_id, path, content,
                len(content), tokens_text, to_halfvec_literal(vec), ["public"])

        log.info("Hello 集合灌入完成：{} 个小节", len(SAMPLE_DOC["sections"]))
        return coll_id


async def search(query: str, coll_id, top_k: int = 3):
    """一条最朴素的向量检索 SQL。"""
    qvec = (await get_embedder().embed([query]))[0]

    sql = """
    SELECT chunk_id, heading_path, content, char_start, char_end,
           1 - (embedding <=> $1::halfvec(1024)) AS score
    FROM chunks
    WHERE collection_id = $2
      AND level         = 1
      AND acl_labels   && $3::text[]
    ORDER BY embedding <=> $1::halfvec(1024)
    LIMIT $4;
    """

    async with acquire() as conn:
        return await conn.fetch(sql, to_halfvec_literal(qvec), coll_id, ["public"], top_k)


async def main():
    await init_pool()
    try:
        coll_id = await seed()

        print("\n" + "=" * 60)
        print("查询：兵马俑的门票多少钱")
        print("=" * 60)
        rows = await search("兵马俑的门票多少钱", coll_id)
        for i, r in enumerate(rows, 1):
            print(f"\n[{i}] 分数 {r['score']:.4f}")
            print(f"    标题路径: {' > '.join(r['heading_path'])}")
            print(f"    字符区间: [{r['char_start']}, {r['char_end']}]")
            print(f"    内容: {r['content'][:60]}...")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())