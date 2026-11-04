-- 第八章：对外接口。两张表：调用方的 API Key，和查询日志。

-- 一把 key 一行。明文不存，只存哈希
CREATE TABLE api_keys (
    key_id      text PRIMARY KEY,                    -- key 的前 8 位，用来在日志里指认是哪把
    key_hash    text NOT NULL UNIQUE,                -- sha256(完整 key)
    caller_id   text NOT NULL,                       -- 调用方名字，例如 zhixing-travel
    labels      text[] NOT NULL DEFAULT '{public}',  -- 这个调用方的权限标签，服务端说了算
    collections text[] NOT NULL DEFAULT '{}',        -- 允许访问的集合短名，空数组 = 一个都不给
    rate_limit  int  NOT NULL DEFAULT 60,            -- 每分钟请求数上限
    status      text NOT NULL DEFAULT 'active',      -- active | disabled
    note        text,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- 每个请求一行。只记 ID 和指标，不记证据原文
CREATE TABLE query_logs (
    request_id uuid PRIMARY KEY,
    caller_id  text NOT NULL,
    collection text NOT NULL,
    query      text NOT NULL,
    top_k      int  NOT NULL,
    status     int  NOT NULL,                        -- HTTP 状态码
    abstain    boolean,
    evidences  int,
    chunk_ids  text[] NOT NULL DEFAULT '{}',
    timings    jsonb  NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX query_logs_recent ON query_logs (created_at DESC);
CREATE INDEX query_logs_caller ON query_logs (caller_id, created_at DESC);