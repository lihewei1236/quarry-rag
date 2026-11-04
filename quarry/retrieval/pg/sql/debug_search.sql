-- 调试用。和 hybrid_search.sql 的前三段完全一致，
-- 区别只在于把三路结果都带回去，而不是只返回融合后的 top_k。
-- 参数含义见 hybrid_search.sql，$11 在这里是每一列展示多少条。

WITH dense AS (
  SELECT chunk_id, row_number() OVER (ORDER BY dist) AS rk, dist
  FROM (
    SELECT chunk_id, embedding <=> $4::halfvec(1024) AS dist
    FROM chunks
    WHERE collection_id = $1 AND level = 1
      AND acl_labels && $2::text[] AND meta @> $3::jsonb
    ORDER BY embedding <=> $4::halfvec(1024)
    LIMIT $6
  ) d
),
sparse AS (
  SELECT chunk_id, row_number() OVER (ORDER BY ts_score DESC) AS rk, ts_score
  FROM (
    SELECT chunk_id, ts_rank_cd(tokens, to_tsquery('simple', $5)) AS ts_score
    FROM chunks
    WHERE collection_id = $1 AND level = 1
      AND acl_labels && $2::text[] AND meta @> $3::jsonb
      AND tokens @@ to_tsquery('simple', $5)
    ORDER BY ts_rank_cd(tokens, to_tsquery('simple', $5)) DESC
    LIMIT $7
  ) s
),
merged AS (
  SELECT chunk_id,
         COALESCE($8::float8 / ($10::int + d.rk), 0)
       + COALESCE($9::float8 / ($10::int + s.rk), 0) AS score,
         d.rk AS dense_rank, s.rk AS sparse_rank
  FROM dense d FULL OUTER JOIN sparse s USING (chunk_id)
),
dense_rows AS (
  SELECT d.rk, round((1 - d.dist)::numeric, 4) AS score,
         c.chunk_id, c.heading_path, left(c.content, 70) AS preview
  FROM dense d
  JOIN chunks c ON c.collection_id = $1 AND c.chunk_id = d.chunk_id
),
sparse_rows AS (
  SELECT s.rk, round(s.ts_score::numeric, 4) AS score,
         c.chunk_id, c.heading_path, left(c.content, 70) AS preview
  FROM sparse s
  JOIN chunks c ON c.collection_id = $1 AND c.chunk_id = s.chunk_id
),
merged_rows AS (
  SELECT row_number() OVER (ORDER BY m.score DESC) AS rk,
         round(m.score::numeric, 5) AS score,
         m.dense_rank, m.sparse_rank,
         c.chunk_id, c.heading_path, left(c.content, 70) AS preview
  FROM merged m
  JOIN chunks c ON c.collection_id = $1 AND c.chunk_id = m.chunk_id
  ORDER BY m.score DESC
  LIMIT $11
)
SELECT
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.rk) FROM dense_rows  x) AS dense,
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.rk) FROM sparse_rows x) AS sparse,
  (SELECT jsonb_agg(to_jsonb(x) ORDER BY x.rk) FROM merged_rows x) AS merged;