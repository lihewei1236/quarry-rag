-- chunks 的身份从 chunk_id 改为 (collection_id, chunk_id)。
-- 原因：chunk_id 由内容决定，同样的内容在不同集合里（蓝绿切换的新旧集合、
-- 不同租户的同名集合）会算出相同的 chunk_id。只用 chunk_id 做主键时，
-- 新集合的块写不进去，而且不报错。详见第二章 2.7.5。

ALTER TABLE chunks DROP CONSTRAINT chunks_pkey;
ALTER TABLE chunks ADD CONSTRAINT chunks_pkey PRIMARY KEY (collection_id, chunk_id);