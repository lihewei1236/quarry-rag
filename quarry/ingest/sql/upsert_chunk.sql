-- 幂等写入一个块。已存在则只更新可变字段，不重写 content 和 embedding。
-- 因为 chunk_id 由内容算出来，ID 相同就意味着内容相同，重写只是浪费。
--
-- ⚠️ 冲突目标是 (collection_id, chunk_id)，不是 chunk_id。
--    原因见第二章 2.8.3：chunk_id 只在集合内唯一。

INSERT INTO chunks (
  chunk_id, document_id, collection_id, doc_version,
  level, parent_id, ord, heading_path,
  content, char_start, char_end,
  tokens, embedding,
  acl_labels, meta, updated_at
)
VALUES (
  $1, $2, $3, $4,
  $5, $6, $7, $8,
  $9, $10, $11,
  to_tsvector('simple', $12::text), $13::halfvec(1024),
  $14, $15, now()
)
ON CONFLICT (collection_id, chunk_id) DO UPDATE SET
  document_id = EXCLUDED.document_id,
  doc_version = EXCLUDED.doc_version,
  parent_id   = EXCLUDED.parent_id,
  ord         = EXCLUDED.ord,
  char_start  = EXCLUDED.char_start,
  char_end    = EXCLUDED.char_end,
  acl_labels  = EXCLUDED.acl_labels,
  meta        = EXCLUDED.meta,
  updated_at  = now();

-- 不在更新列表里的四项：content、heading_path、tokens、embedding。
-- chunk_id 相同意味着这四项本来就相同，重写只是白花时间。