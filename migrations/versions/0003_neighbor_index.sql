-- 邻块索引改成 (document_id, ord)，服务"同一文档里 ord 相邻的父块"这个查询。
DROP INDEX IF EXISTS chunks_neighbors;

CREATE INDEX chunks_neighbors ON chunks (document_id, ord) WHERE level = 0;