-- 取一批父块里的易变数值提及，连同匹配到的官方值（没有匹配就是 NULL）。
-- 参数：$1 collection_id  $2 父块 chunk_id 数组  $3 caller_labels
-- 权限跟着块走：调用方看不到提及所在的块，就拿不到这条提及。
SELECT m.chunk_id, m.span_start, m.span_end, m.entity_name, m.attribute, m.qualifier,
       m.value       AS written_value,
       f.entity_name AS official_name,
       f.value       AS official_value,
       f.verified_at,
       f.source_url
FROM fact_mentions m
JOIN chunks c
  ON c.collection_id = m.collection_id
 AND c.chunk_id      = m.chunk_id
 AND c.acl_labels   && $3::text[]
LEFT JOIN facts f
  ON f.collection_id = m.collection_id
 AND f.match_keys   @> ARRAY[m.entity_norm]
 AND f.attribute     = m.attribute
 AND f.qualifier     = m.qualifier
WHERE m.collection_id = $1
  AND m.chunk_id      = ANY($2::text[])
ORDER BY m.chunk_id, m.span_start;