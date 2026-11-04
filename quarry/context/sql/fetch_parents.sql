-- 按 chunk_id 批量取父块。
-- 参数：$1 collection_id  $2 父块 chunk_id 数组  $3 caller_labels
-- char_start 本章用不到，第六章要靠它算"命中块在父块里的偏移"。
SELECT chunk_id, document_id, ord, content, char_start
FROM chunks
WHERE collection_id = $1
  AND chunk_id      = ANY($2::text[])
  AND level         = 0
  AND acl_labels   && $3::text[];