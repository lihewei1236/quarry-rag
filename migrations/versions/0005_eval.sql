-- 第七章：评测。三张表只服务评测，删掉不影响检索链路。

-- 标注集：一个问题一行
CREATE TABLE eval_queries (
    collection_id uuid NOT NULL REFERENCES collections (id),
    qid           text NOT NULL,                      -- 由问题内容算出，重复导入不会变
    query         text NOT NULL,
    kind          text NOT NULL,                      -- answerable 有答案 | unanswerable 无答案
    gold_chunks   text[] NOT NULL DEFAULT '{}',       -- 应该被检索到的父块；无答案问题为空
    note          text,
    PRIMARY KEY (collection_id, qid)
);

-- 每跑一次评测记一行，配置和指标都存下来，方便以后对照
CREATE TABLE eval_runs (
    run_id        text PRIMARY KEY,                   -- 例如 20260918-153012-baseline
    label         text NOT NULL,
    collection_id uuid NOT NULL REFERENCES collections (id),
    config        jsonb NOT NULL,
    metrics       jsonb NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX eval_runs_label ON eval_runs (collection_id, label, created_at DESC);

-- 每次运行里每个问题的结果，用来找出"哪几个问题变好了、哪几个变坏了"
CREATE TABLE eval_results (
    run_id       text NOT NULL REFERENCES eval_runs (run_id) ON DELETE CASCADE,
    qid          text NOT NULL,
    hit_rank     int,                                 -- 金块排在第几条证据，没命中为 NULL
    abstained    boolean NOT NULL,
    evidences    int NOT NULL,
    judge        text,                                -- supported | partial | unsupported
    judge_reason text,
    latency_ms   double precision NOT NULL,
    evidence     jsonb NOT NULL DEFAULT '[]',         -- 前 3 条证据的快照，报告用
    PRIMARY KEY (run_id, qid)
);