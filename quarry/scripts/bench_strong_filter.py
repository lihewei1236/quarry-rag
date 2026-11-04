"""造一批数据，把强过滤下的漏召回现象跑出来。

思路：给每个块预先分配一个"离查询向量的名次"，把 10 个稀有标签块
均匀撒在第 50～850 名之间。iterative_scan=off 时 HNSW 只拿前 ef_search 个
候选再过滤，所以 ef_search 从 40 调到 1000，返回条数会从 0 逐步补到 10。

做完会把造的数据清掉，不影响你现有的语料。
"""
import asyncio
import math
import random

from quarry.common.vectors import to_halfvec_literal
from quarry.infra.db import acquire, close_pool, init_pool

N_TOTAL = 5000
DIM = 1024
BATCH = 500
TOP_K = 10

# 稀有标签块的名次（0 起算），名次越小离查询越近
RARE_POSITIONS = [50, 120, 180, 250, 350, 450, 550, 650, 750, 850]
EF_LIST = [40, 100, 150, 200, 300, 400, 500, 600, 700, 800, 900, 1000]

SIM_TOP = 0.95      # 第 0 名与查询的余弦相似度
SIM_SPAN = 0.90     # 最后一名的相似度 = SIM_TOP - SIM_SPAN = 0.05


def unit(vec):
    n = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / n for v in vec]


def make_vec(q, sim, rng):
    """构造一个与 q 的余弦相似度恰好为 sim 的单位向量。"""
    u = [rng.gauss(0, 1) for _ in range(DIM)]
    dot = sum(a * b for a, b in zip(u, q))
    u = unit([a - dot * b for a, b in zip(u, q)])      # 去掉 q 方向的分量
    ortho = math.sqrt(max(0.0, 1 - sim * sim))
    return unit([sim * a + ortho * b for a, b in zip(q, u)])


async def setup(conn):
    tenant_id = await conn.fetchval(
        """INSERT INTO tenants (slug, name) VALUES ('stress', '压测租户')
           ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name
           RETURNING id""")
    coll_id = await conn.fetchval(
        """INSERT INTO collections (tenant_id, slug, name, embed_model, embed_dim)
           VALUES ($1, 'stress', '压测集合', 'synthetic', 1024)
           ON CONFLICT (tenant_id, slug) WHERE status = 'active'
           DO UPDATE SET name = EXCLUDED.name
           RETURNING id""", tenant_id)
    doc_id = await conn.fetchval(
        """INSERT INTO documents (collection_id, source_uri, sha256, status)
           VALUES ($1, 'synthetic.md', 'x', 'ready')
           ON CONFLICT (collection_id, source_uri, version)
           DO UPDATE SET status = 'ready'
           RETURNING id""", coll_id)

    await conn.execute("DELETE FROM chunks WHERE collection_id = $1", coll_id)

    rng = random.Random(42)
    q = unit([rng.gauss(0, 1) for _ in range(DIM)])
    rare_set = set(RARE_POSITIONS)

    # 打乱插入顺序，避免"按相似度顺序插入"影响 HNSW 建图
    order = list(range(N_TOTAL))
    rng.shuffle(order)

    sql = """INSERT INTO chunks (chunk_id, document_id, collection_id, doc_version,
                                 level, parent_id, ord, heading_path, content,
                                 char_start, char_end, acl_labels, embedding)
             VALUES ($1, $2, $3, 1, 1, 'synthetic-parent', $4, ARRAY['压测'],
                     $5, 0, 1, $6, $7::halfvec(1024))"""
    batch = []
    for n, pos in enumerate(order, 1):
        sim = SIM_TOP - SIM_SPAN * pos / (N_TOTAL - 1)
        vec = make_vec(q, sim, rng)
        labels = ["org:tiny-team"] if pos in rare_set else ["public"]
        batch.append((f"stress-{pos:06d}", doc_id, coll_id, pos, "x",
                      labels, to_halfvec_literal(vec)))
        if len(batch) >= BATCH:
            await conn.executemany(sql, batch)
            batch.clear()
            print(f"  已写入 {n} / {N_TOTAL}")
    if batch:
        await conn.executemany(sql, batch)

    return coll_id, to_halfvec_literal(q)


PROBE_SQL = """
SELECT chunk_id FROM chunks
WHERE collection_id = $1 AND level = 1
  AND acl_labels && ARRAY['org:tiny-team']::text[]
ORDER BY embedding <=> $2::halfvec(1024)
LIMIT $3
"""


async def exact_rare_ranks(conn, coll_id, qvec):
    """精确计算稀有块在本集合里的名次（1 起算）。"""
    rows = await conn.fetch(
        """WITH ranked AS (
             SELECT acl_labels,
                    row_number() OVER (ORDER BY embedding <=> $2::halfvec(1024)) AS rn
             FROM chunks WHERE collection_id = $1 AND level = 1)
           SELECT rn FROM ranked
           WHERE acl_labels && ARRAY['org:tiny-team']::text[]
           ORDER BY rn""", coll_id, qvec)
    return [r["rn"] for r in rows]


async def probe(conn, coll_id, qvec, scan_mode, ef_search):
    """强制走 HNSW，返回 (实际条数, 扫描方式)。"""
    async with conn.transaction():
        await conn.execute(f"SET LOCAL hnsw.iterative_scan = {scan_mode}")
        await conn.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
        await conn.execute("SET LOCAL hnsw.max_scan_tuples = 20000")
        # 堵死顺序扫描和"GIN 过滤后再排序"这两条路，只剩 HNSW
        await conn.execute("SET LOCAL enable_seqscan = off")
        await conn.execute("SET LOCAL enable_bitmapscan = off")
        await conn.execute("SET LOCAL enable_sort = off")

        plan_rows = await conn.fetch("EXPLAIN " + PROBE_SQL, coll_id, qvec, TOP_K)
        plan = "\n".join(r[0] for r in plan_rows)
        used = "HNSW" if "hnsw" in plan.lower() else "其他"

        rows = await conn.fetch(PROBE_SQL, coll_id, qvec, TOP_K)
    return len(rows), used


async def main():
    await init_pool()
    try:
        async with acquire() as conn:
            try:
                print("造数据中，大约需要一分钟")
                coll_id, qvec = await setup(conn)
                await conn.execute("ANALYZE chunks")
                print(f"造好了，{N_TOTAL} 个块，其中 {len(RARE_POSITIONS)} 个带 org:tiny-team\n")

                ranks = await exact_rare_ranks(conn, coll_id, qvec)
                print(f"稀有块的精确名次：{ranks}\n")

                print(f"{'iterative_scan':<16}{'ef_search':<11}{'扫描':<7}"
                      f"{'理论':<7}{'实际':<7}")
                print("-" * 50)
                for ef in EF_LIST:
                    expect = min(TOP_K, sum(1 for r in ranks if r <= ef))
                    n, used = await probe(conn, coll_id, qvec, "off", ef)
                    bar = "█" * n + "·" * (TOP_K - n)
                    print(f"{'off':<16}{ef:<11}{used:<7}{expect:<7}{n:<7}{bar}")

                print("-" * 50)
                for mode in ("relaxed_order", "strict_order"):
                    n, used = await probe(conn, coll_id, qvec, mode, 40)
                    bar = "█" * n + "·" * (TOP_K - n)
                    print(f"{mode:<16}{40:<11}{used:<7}{TOP_K:<7}{n:<7}{bar}")
            finally:
                await conn.execute("DELETE FROM tenants WHERE slug = 'stress'")
                print("\n压测数据已清理")
    finally:
        await close_pool()


if __name__ == "__main__":
    asyncio.run(main())