-- 第六章：事实层。
-- 这几张表只被事实层使用：哪天整层下线，删掉三张表和 facts_enabled 一列即可。

-- 集合级开关：只对第三方语料开启（原因见 6.1.4）
ALTER TABLE collections ADD COLUMN facts_enabled boolean NOT NULL DEFAULT false;

-- 官方值：从官方数据文件导入。一个实体的一个属性（按季节区分）一行
CREATE TABLE facts (
    collection_id uuid NOT NULL REFERENCES collections (id),
    entity_name   text NOT NULL,                  -- 规范名称，展示用
    match_keys    text[] NOT NULL,                -- 规范化后的名称和别名，匹配用
    attribute     text NOT NULL,                  -- ticket_price | open_hours
    qualifier     text NOT NULL DEFAULT '全年',   -- 全年 | 旺季 | 淡季
    value         text NOT NULL,                  -- 规范化后的值：120 / 08:30-18:00
    verified_at   date NOT NULL,                  -- 官方核实日期
    source_url    text,
    PRIMARY KEY (collection_id, entity_name, attribute, qualifier)
);
CREATE INDEX facts_match_keys ON facts USING gin (match_keys);

-- 正文里的易变数值：从父块抽取。span 是相对父块 content 的位置
CREATE TABLE fact_mentions (
    collection_id uuid NOT NULL,
    chunk_id      text NOT NULL,                  -- 父块（level = 0）
    span_start    int  NOT NULL,
    span_end      int  NOT NULL,
    entity_name   text NOT NULL,                  -- 模型给出的实体名
    entity_norm   text NOT NULL,                  -- 规范化后的实体名，和 facts.match_keys 比较
    attribute     text NOT NULL,
    qualifier     text NOT NULL,
    value         text NOT NULL,                  -- 正文里写的值（已规范化）
    quote         text NOT NULL,                  -- = 父块 content[span_start:span_end]
    PRIMARY KEY (collection_id, chunk_id, span_start),
    FOREIGN KEY (collection_id, chunk_id)
        REFERENCES chunks (collection_id, chunk_id) ON DELETE CASCADE
);

-- 抽取记录：哪些父块已经抽过（抽出 0 条的也记）。块内容不变 chunk_id 就不变，不会重复花钱
CREATE TABLE fact_extractions (
    collection_id uuid        NOT NULL,
    chunk_id      text        NOT NULL,
    model         text        NOT NULL,
    mention_count int         NOT NULL,
    extracted_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (collection_id, chunk_id),
    FOREIGN KEY (collection_id, chunk_id)
        REFERENCES chunks (collection_id, chunk_id) ON DELETE CASCADE
);