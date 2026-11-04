-- 混合召回。一次往返完成过滤、双路召回、RRF 融合。
--
-- 参数
--   $1  collection_id   UUID
--   $2  caller_labels   TEXT[]     调用者持有的权限标签
--   $3  meta_filter     JSONB      元数据过滤，没有条件时传 {}
--   $4  query_vec       TEXT       halfvec 字面量
--   $5  query_tsquery   TEXT       Python 端拼好的 OR 表达式
--   $6  dense_k         INT        向量路取多少候选
--   $7  sparse_k        INT        全文路取多少候选
--   $8  weight_dense    FLOAT8
--   $9  weight_sparse   FLOAT8
--   $10 rrf_k           INT        RRF 的衰减常数，常用 60
--   $11 top_k           INT        最终返回多少
--
-- ⚠️ 四个过滤条件在两个子查询里各写一遍，不要提成共享 CTE。原因见 4.3.2。

WITH dense AS (
  -- 先用 HNSW 索引取前 dense_k 名，再给这几十行编号。
  -- 编号放在外层，是为了让内层那个 ORDER BY ... LIMIT 能被索引直接满足。
  SELECT chunk_id,
         row_number() OVER (ORDER BY dist) AS rk,
         dist
  FROM (
    SELECT chunk_id,
           embedding <=> $4::halfvec(1024) AS dist
    FROM chunks
    WHERE collection_id = $1
      AND level         = 1
      AND acl_labels   && $2::text[]
      AND meta          @> $3::jsonb
    ORDER BY embedding <=> $4::halfvec(1024)
    LIMIT $6
  ) d
),
sparse AS (
  SELECT chunk_id,
         row_number() OVER (ORDER BY ts_score DESC) AS rk,
         ts_score
  FROM (
    SELECT chunk_id,
           ts_rank_cd(tokens, to_tsquery('simple', $5)) AS ts_score
    FROM chunks
    WHERE collection_id = $1
      AND level         = 1
      AND acl_labels   && $2::text[]
      AND meta          @> $3::jsonb
      AND tokens        @@ to_tsquery('simple', $5)
    ORDER BY ts_rank_cd(tokens, to_tsquery('simple', $5)) DESC
    LIMIT $7
  ) s
),
merged AS (
  -- FULL OUTER JOIN 保证两路的并集都进入打分。
  -- USING 的连接列在 FULL JOIN 下自动取两边的非空值，不用再写 COALESCE。
  SELECT chunk_id,
         COALESCE($8::float8 / ($10::int + d.rk), 0)
       + COALESCE($9::float8 / ($10::int + s.rk), 0) AS score,
         d.rk       AS dense_rank,
         s.rk       AS sparse_rank,
         d.dist     AS dense_dist,
         s.ts_score AS sparse_score
  FROM dense d
  FULL OUTER JOIN sparse s USING (chunk_id)
)
SELECT c.chunk_id, c.document_id, c.parent_id,
       c.heading_path, c.content, c.char_start, c.char_end,
       m.score, m.dense_rank, m.sparse_rank,
       1 - m.dense_dist AS dense_score,
       m.sparse_score
FROM merged m
JOIN chunks c
  ON c.collection_id = $1        -- ⚠️ 这一行少不得，原因见 4.3.4 第 ④ 点
 AND c.chunk_id      = m.chunk_id
ORDER BY m.score DESC
LIMIT $11;