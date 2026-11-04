-- 取每个父块前后 radius 个相邻父块（同一文档、ord 相邻），走 0003 迁移建的索引。
-- 参数：$1 collection_id  $2 文档 ID 数组  $3 中心 ord 数组（与 $2 一一对应）
--       $4 radius          $5 caller_labels
SELECT c.chunk_id, c.document_id, c.ord, c.content, t.center_ord
FROM unnest($2::uuid[], $3::int[]) AS t(doc_id, center_ord)
JOIN chunks c
  ON c.collection_id = $1
 AND c.document_id   = t.doc_id
 AND c.level         = 0
 AND c.ord BETWEEN t.center_ord - $4 AND t.center_ord + $4
 AND c.ord <> t.center_ord
WHERE c.acl_labels && $5::text[]
ORDER BY c.document_id, c.ord;