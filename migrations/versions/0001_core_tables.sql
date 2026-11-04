-- 本章直接给的初始迁移。表结构的每个字段为什么存在，第二章讲。

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================ 租户 ============================
CREATE TABLE tenants (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug        TEXT UNIQUE NOT NULL,
  name        TEXT NOT NULL,
  quota       JSONB NOT NULL DEFAULT '{}'::jsonb,
  status      TEXT NOT NULL DEFAULT 'active',
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================ 集合 ============================
CREATE TABLE collections (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
  slug           TEXT NOT NULL,
  name           TEXT NOT NULL,

  embed_model    TEXT NOT NULL,
  embed_dim      INT  NOT NULL,

  chunk_policy   JSONB NOT NULL DEFAULT '{}'::jsonb,
  meta_schema    JSONB NOT NULL DEFAULT '{}'::jsonb,
  abstain_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  default_acl_labels TEXT[] NOT NULL DEFAULT '{public}',

  status         TEXT NOT NULL DEFAULT 'active',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT collections_dim_ck CHECK (embed_dim = 1024)
);

CREATE UNIQUE INDEX collections_one_active
  ON collections (tenant_id, slug) WHERE status = 'active';
CREATE UNIQUE INDEX collections_one_building
  ON collections (tenant_id, slug) WHERE status = 'building';

-- ============================ 文档 ============================
CREATE TABLE documents (
  id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  collection_id  UUID NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  source_uri     TEXT NOT NULL,
  archive_uri    TEXT,
  title          TEXT,
  sha256         TEXT NOT NULL,
  version        INT  NOT NULL DEFAULT 1,

  status         TEXT NOT NULL DEFAULT 'uploaded',
  stage_error    TEXT,
  parser_tier    TEXT,

  meta           JSONB NOT NULL DEFAULT '{}'::jsonb,
  acl_labels     TEXT[] NOT NULL DEFAULT '{}',
  page_count     INT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  indexed_at     TIMESTAMPTZ,

  UNIQUE (collection_id, source_uri, version)
);

CREATE INDEX documents_by_status ON documents (collection_id, status);

-- ============================ 文本块 ============================
CREATE TABLE chunks (
  chunk_id      TEXT PRIMARY KEY,
  document_id   UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  collection_id UUID NOT NULL,
  doc_version   INT  NOT NULL,

  level         SMALLINT NOT NULL,       -- 0 = 父块，1 = 子块
  parent_id     TEXT,                    -- 子块指向父块；父块为 NULL
  ord           INT NOT NULL,

  heading_path  TEXT[],
  content       TEXT NOT NULL,
  char_start    INT NOT NULL,
  char_end      INT NOT NULL,

  tokens        TSVECTOR,
  embedding     halfvec(1024),

  acl_labels    TEXT[] NOT NULL DEFAULT '{}',
  meta          JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT chunks_span_ck  CHECK (char_end > char_start),
  CONSTRAINT chunks_level_ck CHECK (level IN (0, 1)),
  CONSTRAINT chunks_parent_ck CHECK (
    (level = 1 AND parent_id IS NOT NULL) OR (level = 0 AND parent_id IS NULL)
  )
);

-- ============================ 索引 ============================
CREATE INDEX chunks_embed_hnsw ON chunks
  USING hnsw (embedding halfvec_cosine_ops)
  WITH (m = 16, ef_construction = 64)
  WHERE level = 1;

CREATE INDEX chunks_tokens_gin ON chunks
  USING gin (tokens) WHERE level = 1;

CREATE INDEX chunks_acl_gin ON chunks USING gin (acl_labels);

CREATE INDEX chunks_meta_gin ON chunks USING gin (meta jsonb_path_ops);

CREATE INDEX chunks_scope ON chunks (collection_id, doc_version, level);

CREATE INDEX chunks_parent ON chunks (parent_id) WHERE level = 1;

CREATE INDEX chunks_neighbors ON chunks (document_id, heading_path, ord)
  WHERE level = 0;