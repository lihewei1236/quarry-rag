# quarry-rag

一个开源、可自部署、面向多应用复用的企业级 RAG 检索平台。

**它不生成答案，只交付证据**。给定一次查询、一个集合和调用方的 API Key，它返回一组经过混合召回、重排和同节去重的证据：每条带着命中原文和能对回原文的字符区间、交给大模型读的上下文、各路分数，以及其中易变数值的核实状态与核实日期；整个响应再附一个"建议拒答"的信号。

上游的"知行旅行助手"拿这些证据去写行程、校验预算；换一个上游应用，平台不用改。

---

## 目录

- [它长什么样](#它长什么样)
- [特性](#特性)
- [技术栈](#技术栈)
- [快速开始](#快速开始)
- [配置](#配置)
- [常用命令](#常用命令)
- [对外接口](#对外接口)
- [上游怎么接](#上游怎么接)
- [项目结构](#项目结构)
- [数据库迁移](#数据库迁移)
- [测试](#测试)
- [评测与调优](#评测与调优)
- [部署](#部署)
- [能力边界](#能力边界)
- [明确不选的技术](#明确不选的技术)

---

## 它长什么样

一次检索交给上游的东西（省略了部分字段，数值为演示数据）：

```json
{
  "request_id": "7c1e9a52-0b7f-4d0e-9d1a-2f3b4c5d6e7f",
  "query": "兵马俑门票多少钱",
  "abstain": {"should_abstain": false, "reason": null, "top_dense_score": 0.7412, "threshold": 0.52},
  "evidences": [
    {
      "chunk_id": "9c1e…",
      "text": "兵马俑博物馆位于临潼区，门票 120 元，旺季开放时间 8:30 至 18:00……",
      "context": "……门票 〔F1 门票价格〕，旺季开放时间 〔F2 开放时间〕……",
      "context_level": "neighbors",
      "citation": {
        "source_uri": "travel-guide/西安.md",
        "heading_path": ["西安", "游览", "秦始皇陵兵马俑"],
        "char_start": 5120,
        "char_end": 5388,
        "locator_kind": "char",
        "verified_at": "2026-09-01"
      },
      "facts": [
        {"ref": "F1", "entity": "秦始皇帝陵博物院", "label": "门票价格", "qualifier": "旺季",
         "value": "150", "status": "verified", "verified_at": "2026-09-01",
         "written_values": ["120"]}
      ],
      "score": 0.0325,
      "rerank_score": 0.912
    }
  ],
  "timings": {"recall_ms": 142.8, "rerank_ms": 231.5, "facts_ms": 3.2, "total_ms": 389.1}
}
```

注意 `text` 里仍是原文写的 `120 元`，它只用来在原文里高亮；交给模型读的 `context` 在同一位置已经换成 `〔F1 门票价格〕`，官方核实过的 `150` 在 `facts` 里。

---

## 特性

**只返回证据，不替上游作答**。平台里没有生成端点，也不做查询改写。连"把证据拼进提示词"这一步，都放在交给上游的客户端文件里。这条边界决定了接口的响应形状、拒答信号的职责划分，也决定了评测只评证据、不评答案。

**两个容器起全栈**。基础设施只有 `postgres + pgvector` 和 `redis-stack` 两个现成镜像。向量检索、全文检索、元数据与权限过滤、事实数据、评测记录、API Key、查询日志都在同一个 PostgreSQL 里；Redis 只管接入队列和限流计数。

**一条 SQL 完成过滤、双路召回与 RRF 融合**。向量召回和全文召回是同一条 SQL 里的两个子查询，`FULL OUTER JOIN` 之后按倒数排名融合，一次数据库往返拿到结果。四个过滤条件在两路里各写一遍，不提成共享 CTE：共享 CTE 被引用两次就会被物化，向量那一路从此用不上 HNSW，而在几千行数据上完全看不出来。

**内容决定标识，接入管道幂等**。`chunk_id` 由"集合短名、来源、标题路径、归一化后的正文"算 sha256 得出，跨进程、跨机器都稳定。文档级 sha256 让没改过的文件整条管道跳过，块级 `chunk_id` 决定要不要重算向量：改一个字只重算那一个子块。

**权限允许制，从数据库一路守到接口**。块上冗余存一份权限标签，混合召回以及父块、邻块、事实提及的回捞 SQL，都在数据库层用数组重叠强制过滤。接口层的权限标签跟着 API Key 走，请求体里夹带的标签一律无效。11 条红线用例里有 9 条和权限有关，而且都带反向验证。

**事实层：遮住易变数值，补上核实过的值**。离线从正文抽出门票价格和开放时间，对照运营维护的官方数据；查询时把上下文里的数字换成 `〔F1 门票价格〕`，真实值、核实日期和"已核实 / 已过期 / 未核实"三种状态一起交给上游。

**先有量具再调参**。标注集、基线、逐题对照、回归闸门、单文件 HTML 评测报告。重排值不值得开、事实层有没有拖累别的指标，用数字回答而不是感觉。

---

## 技术栈

| 层 | 选择 |
|---|---|
| 运行时 | Python 3.13（代码要求 ≥ 3.11）、uv、FastAPI + uvicorn、全链路 asyncio |
| 存储与检索 | PostgreSQL 17 + pgvector 0.8.6：`halfvec(1024)` + HNSW、`tsvector` + GIN、数组与 JSONB 的 GIN、部分索引 |
| 队列与限流 | Redis Stack 7.4：Stream + 消费者组、按 key 的固定窗口计数 |
| 模型档位 | 向量化 `text-embedding-v4`、重排 `qwen3-rerank`、抽取 `qwen-flash`、裁判 `qwen-plus`，默认都接阿里云百炼，共用一个 API Key |
| 中文分词 | jieba，应用层分词（写入侧和查询侧共用同一份归一化） |
| 迁移与测试 | Alembic（DDL 写在同名 `.sql` 里）、pytest + pytest-asyncio、ruff |

---

## 快速开始

### 前置

- Docker Desktop（Windows 用 WSL 2 后端），给它至少 4 GB 内存
- [uv](https://docs.astral.sh/uv/)
- 一把阿里云百炼 API Key（没有也能跑通环境搭建，见下）

### 1. 装依赖

```powershell
uv sync --extra parse --extra dev
```

两个 `--extra` 都要写：`uv sync` 会把环境同步成"恰好是你这次要求的样子"。

### 2. 起容器

```powershell
Copy-Item .env.example .env
# 打开 .env，把 LLM_API_KEY 换成你自己的
docker compose up -d
docker compose ps        # 两个容器都要是 (healthy)
```

```bash
# macOS / Linux
cp .env.example .env
docker compose up -d
docker compose ps
```

### 3. 建表

```powershell
uv run alembic upgrade head
uv run python verify_env.py
```

`verify_env.py` 五项全是 ✅ 才算环境打通。没有 API Key 时，把 `.env` 里的 `EMBED_BACKEND` 改成 `fake` 可以跑通这一步，但检索结果没有语义意义，正式使用前要改回 `api`。

### 4. 灌语料

```powershell
uv run python -m quarry.scripts.fetch_corpus --check    # 先看网络通不通
uv run python -m quarry.scripts.fetch_corpus            # 抓中文维基导游
uv run python -m quarry.scripts.ingest_corpus           # 接入 corpus/ 下的所有文档
```

抓不到语料时，`corpus/` 下自己放几份 Markdown 也能跑（按 `travel-guide` / `corp-policy` / `internal-notes` 三个子目录分放，目录名决定集合和默认权限标签）。

### 5. 跑一次检索

```powershell
uv run python -m quarry.scripts.try_pipeline "兵马俑的门票多少钱"
```

输出里会看到拒答建议、每条证据的来源与字符区间、上下文档位和分段耗时。

### 6. 起服务

```powershell
uv run uvicorn quarry.api.app:app --host 127.0.0.1 --port 8100 --reload
```

- 健康检查：`http://localhost:8100/healthz`
- 接口文档：`http://localhost:8100/docs`
- 检索调试台：`http://localhost:8100/web/debug.html`

⚠️ 所有命令都在**项目根目录**执行：脚本里的 `corpus/`、`quarry/web` 等路径都是相对当前目录的。

---

## 配置

所有配置在 `.env` 里，代码通过 `quarry/infra/settings.py` 统一读取，业务代码不直接读环境变量。密钥用 `SecretStr` 包裹，打日志不会泄漏。

| 组 | 主要项 | 说明 |
|---|---|---|
| 数据库 | `POSTGRES_HOST/PORT/DB/USER/PASSWORD`、`POOL_MIN_SIZE`、`POOL_MAX_SIZE` | 宿主机连库用 5433 |
| Redis | `REDIS_HOST/PORT/DB` | 宿主机连 6380 |
| 向量化 | `LLM_BASE_URL`、`LLM_API_KEY`、`MODEL_EMBED`、`EMBED_DIM`、`EMBED_BACKEND` | 维度必须是 1024；`fake` 用于无 key 时跑通环境 |
| 重排 | `RERANK_BACKEND`、`RERANK_MODEL`、`RERANK_URL`、`RERANK_CANDIDATES`、`RERANK_TIMEOUT_S` | `noop` 关闭（也是对照组），`api` 走托管接口 |
| 上下文 | `CONTEXT_MAX_CHARS`、`NEIGHBOR_RADIUS` | 上下文总字数预算、邻块半径 |
| 事实层 | `FACT_MODEL`、`FACT_BASE_URL`、`FACT_CONCURRENCY`、`FACT_TTL_DAYS` | 官方值超过 TTL 天数标记为已过期 |
| 评测 | `JUDGE_MODEL`、`JUDGE_BASE_URL`、`EVAL_TOP_K` | 裁判模型只判断证据够不够 |
| 对外接口 | `API_REQUEST_TIMEOUT_S`、`API_MAX_TOP_K`、`API_LOG_QUERIES` | 单请求总时限、`top_k` 封顶、是否记日志 |
| 应用 | `APP_ENV`、`LOG_LEVEL`、`DEFAULT_TOP_K` | `production` 时调试接口自动关闭，日志不打变量值 |

四个模型档位的 `*_API_KEY` 留空即复用 `LLM_API_KEY`。换供应商只改 `.env`，业务代码一行不动。

---

## 常用命令

所有脚本都用 `uv run python -m quarry.scripts.<名字>` 运行。

**接入**

| 命令 | 作用 |
|---|---|
| `fetch_corpus [--check] [--proxy …] [--resume]` | 抓中文维基导游语料，写清单文件 |
| `try_parse <文件>` / `try_split <文件>` | 试解析 / 试切分，顺带校验区间 |
| `ingest_corpus` | 接入 `corpus/` 下所有文档，幂等，可反复跑 |
| `enqueue_one` | 往接入队列里塞一条消息（配合 `python -m quarry.ingest.worker`） |
| `export_snapshot <集合>` | 导出可校验、可复现的索引快照 |

**检索**

| 命令 | 作用 |
|---|---|
| `try_hybrid "问题"` | 只跑混合召回，看两路各自的贡献 |
| `try_pipeline "问题" [集合] [--no-facts]` | 跑完整链路：证据、引用、事实、分段耗时 |
| `bench_search` | 压测检索本身（默认缓存查询向量） |
| `bench_strong_filter` | 造数据复现强过滤下的漏召回 |

**排序与事实**

| 命令 | 作用 |
|---|---|
| `verify_rerank` | 重排冒烟测试，确认"真的在重排" |
| `ab_rerank` | 重排开 / 关对照：排序变了多少、多花了多少时间 |
| `verify_citations` | 全量校验引用区间和原文逐字相符 |
| `calibrate_abstain [--write]` | 标定拒答阈值并写进集合配置 |
| `extract_facts [--dry-run --limit 5]` | 从正文抽取易变数值 |
| `review_facts --n 20` | 抽样核对抽取准确率 |
| `import_official_facts` | 导入 `official/<集合>.csv`，打印核验状态统计 |

**评测与交付**

| 命令 | 作用 |
|---|---|
| `make_eval_draft --n 50` | 生成评测问题草稿（需人工筛选） |
| `import_eval_set` | 导入标注集 |
| `run_eval --label <名字> [--judge]` | 跑一次评测 |
| `compare_eval --base <A> --new <B> [--gate]` | 逐题对照 + 回归闸门 |
| `render_report --base <A> --new <B>` | 生成单文件 HTML 报告 |
| `bench --concurrency 4 --requests 60` | 并发压测，分阶段 P50/P95/P99 |
| `create_api_key --caller … / --list / --revoke …` | 发 key、列 key、停用 key |

---

## 对外接口

对上游开放两个端点，都要带 `Authorization: Bearer <key>`。

### `POST /v1/search`

```json
{
  "collection": "travel-guide",
  "query": "兵马俑门票多少钱",
  "top_k": 5,
  "filters": {"city": "西安"},
  "use_rerank": true,
  "use_facts": true
}
```

**请求体里没有权限字段**。调用方的权限标签和可访问集合由 API Key 决定，请求里写了也会被丢掉。

响应形状见[它长什么样](#它长什么样)。

### `GET /v1/collections`

列出这把 key 能访问的集合，接入时用它确认权限。

### 错误

所有错误都是固定形状，上游按 `code` 分支，按 `request_id` 找日志：

```json
{"error": {"code": "forbidden", "message": "…", "request_id": "7c1e…"}}
```

| 状态码 | `code` | 该不该重试 |
|---|---|---|
| 401 / 403 / 404 / 422 | `unauthorized` / `forbidden` / `not_found` / `invalid_request` | ❌ |
| 429 | `rate_limited` | ✅ 按 `Retry-After` 等待 |
| 500 / 504 | `internal_error` / `timeout` | ✅ 可重试 |

### 其他路由

| 路由 | 说明 |
|---|---|
| `GET /healthz` | 健康检查，依赖不通返回 503 |
| `POST /v1/documents`、`GET /v1/documents/{id}` | 上传文档与查状态 |
| `POST /debug/search`、`/web/debug.html` | 检索调试台，仅 `APP_ENV=development` 可用 |

---

## 上游怎么接

三样东西：平台发的一把 API Key、复制进项目的 `sdk/quarry_client.py`、一段教模型读懂占位符的提示词约定。

```python
from quarry_client import QuarryClient, render_for_prompt

async with QuarryClient("http://localhost:8100", api_key="qr_...") as client:
    result = await client.search("travel-guide", "兵马俑门票多少钱", top_k=5)

prompt_block = render_for_prompt(result)   # 直接放进上游自己的提示词
```

客户端里已经写好了重试策略（401 不重试、429 按 `Retry-After` 等、5xx 指数退避加抖动）、错误分类和提示词拼装。完整示例见 `sdk/example.py`。

给模型的约定：上下文里形如 `〔F1 门票价格〕` 的占位符，代表一个会随时间变化的数值，真实值在同一条证据的 `facts` 里按 `ref` 对应。`verified` 直接用并注明核实日期；`expired` 用但提醒以官方公布为准；`unverified` 用但注明来自第三方、未经核实。`render_for_prompt()` 会替上游把这段拼好。

---

## 项目结构

```text
quarry-rag/
├── docker-compose.yml / Dockerfile / docker/init-db.sql
├── pyproject.toml / uv.lock / alembic.ini
├── .env.example                 # 复制成 .env 再填 key
├── verify_env.py                # 环境自检
├── migrations/                  # Alembic，DDL 写在同名 .sql 里
├── quarry/
│   ├── common/                  # 纯函数：归一化、块标识、向量字面量、计时
│   ├── infra/                   # 配置、日志、连接池、向量化客户端、线程池
│   ├── ingest/                  # 解析、切分、幂等索引、接入队列与 worker
│   ├── retrieval/               # Hit 与协议、查询侧分词、混合召回 SQL、Fake 实现
│   ├── rerank/                  # 协议、托管实现、空实现
│   ├── context/                 # 同节去重、父块与邻块、上下文预算、引用、拒答
│   ├── facts/                   # 契约、抽取、官方数据、查询时遮蔽
│   ├── pipeline.py              # 链路总装，对外只有 search_evidence()
│   ├── eval/                    # 标注集、指标、裁判、运行器、HTML 报告
│   ├── api/                     # 应用装配、路由、鉴权限流、契约、查询日志
│   ├── web/debug.html           # 检索调试台
│   └── scripts/                 # 各类试跑、校验、标定、评测脚本
├── sdk/                         # 交给上游的单文件客户端与示例
├── tests/                       # unit / contract / redlines
├── corpus/                      # 语料，按集合分目录
├── official/                    # 官方数据 CSV，文件名 = 集合短名
├── eval/                        # 标注集与评测报告
└── snapshots/                   # 索引快照
```

**依赖只往一个方向走**：`common/` 最底层，不 import 项目里任何其他子包；检索链路上的子包只往下依赖；把它们串起来的只有 `pipeline.py`，链路里的子包都不许反过来 import 它；`api/`、`eval/` 和脚本站在链路外面，只通过 `search_evidence()` 进入检索。

---

## 数据库迁移

| 版本 | 内容 |
|---|---|
| 0001 | 租户、集合、文档、文本块四张核心表和索引 |
| 0002 | 块主键改成 `(collection_id, chunk_id)` |
| 0003 | 邻块索引改成 `(document_id, ord)` |
| 0004 | 事实层三张表，集合加 `facts_enabled` |
| 0005 | 评测三张表 |
| 0006 | API Key 与查询日志 |

```powershell
uv run alembic upgrade head      # 升到最新
uv run alembic current           # 看当前版本
uv run alembic downgrade -1      # 回退一步
```

每个迁移都写了可用的 `downgrade`，DDL 放在和迁移同名的 `.sql` 文件里（原生 SQL 更好读，也能直接粘进 psql 调试）。

---

## 测试

```powershell
uv run pytest tests/ -v
```

三组测试，一共 26 条：

| 目录 | 内容 | 要不要连库 |
|---|---|---|
| `tests/unit/` | 事实层遮蔽、评测指标、接口契约与客户端重试 | 否 |
| `tests/contract/` | 任何 `Retriever` 实现都要满足的行为 | 否 |
| `tests/redlines/` | 11 条权限与行为红线 | 是 |

**红线挂了就是权限被绕过，不是普通 bug**。每条红线都成对写：既验证"不该看到的看不到"，也验证"该看到的看得到"。只测前一半的话，把过滤条件写成永远为假也能全绿。

---

## 评测与调优

```powershell
uv run python -m quarry.scripts.import_eval_set                              # 导入标注集
uv run python -m quarry.scripts.run_eval --label baseline --no-rerank --no-facts
uv run python -m quarry.scripts.run_eval --label full --judge
uv run python -m quarry.scripts.compare_eval --base baseline --new full --gate
uv run python -m quarry.scripts.render_report --base baseline --new full
```

指标包括召回率、MRR、首位命中率、误拒率、正确拒答率、证据支撑率和延迟分位数。命中判定用"引用区间落在金块区间之内"，不比 `chunk_id`：检索返回的是子块，标注的是父块。

回归闸门检查召回率、正确拒答率、证据支撑率三项，掉超过阈值就以非 0 退出，可以直接挂进 CI：

```yaml
- run: uv run python -m quarry.scripts.run_eval --label ci --judge
- run: uv run python -m quarry.scripts.compare_eval --base baseline --new ci --gate
```

报告是一个自带数据的单文件 HTML：可以进 Git、贴进 PR、当附件发出去，双击就能打开，不需要起服务。

---

## 部署

```powershell
docker compose build api
docker compose run --rm api uv run --no-sync alembic upgrade head
docker compose up -d api
docker compose ps
```

⚠️ 容器里的 `localhost` 是容器自己。给容器单独准备一份环境变量文件，把地址改成 compose 的服务名、端口改成容器内部端口：

```ini
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
REDIS_HOST=redis
REDIS_PORT=6379
```

**迁移不要放进容器的启动命令里**，多个实例同时启动会一起去跑。单独执行一次即可。

上线前的几项检查：迁移到最新、`.env` 里没有占位值、`APP_ENV=production`、每个上游一把独立 key 且集合白名单最小化、`/healthz` 返回 200、26 条测试全绿、回归闸门跑过一次、查询日志定了保留期、做过一次备份。

---

## 能力边界

明确不做的事：

- **不做答案生成**。没有生成端点，不做查询改写和 HyDE。提示词拼装由客户端文件完成，调模型、写答案是上游的事。
- **不做多模态检索**。图片、音视频不在语料范围内。
- **解析只覆盖两档**。Markdown 与纯文本（字符级引用）、Word（段落级引用）。PDF、Excel 给了思路但没有注册进解析器；扫描件和复杂版式只给协议。
- **不做实时数据源**。事实层的官方值来自离线维护的数据文件，查询时不联网核验。超过 TTL 的值标成"已过期"照常交付，不会被当成新的。
- **事实层只管两类数值**。门票价格和开放时间，季节只分全年、旺季、淡季；只对打开开关的第三方语料集合生效。
- **不做跨语言检索**。语料与查询默认中文。
- **不做多版本并存**。文档出新版本后旧版本的块直接删除，文档行留着审计；要比较两套索引走集合级蓝绿切换。
- **只有一种接入方式**。REST 接口加一个 Python 客户端文件，不提供 MCP 服务端或其他语言的 SDK。
- **限流不精确**。按 key 的每分钟固定窗口计数，窗口交界处最多能放进两倍请求；它防的是上游写出死循环，不是计费。
- **性能结论有适用规模**。所有实验在教学语料（几十份文档、一千来个子块）和 5000 块的合成压测数据上得出；"单机 PostgreSQL 能承载几百万到千万级子块"是经验值，未经实测。

---

## 明确不选的技术

| 不选 | 为什么 | 什么时候重新考虑 |
|---|---|---|
| Elasticsearch / OpenSearch | 第二个检索后端意味着双写一致性：发件箱、积压监控、最终一致窗口。本项目的规模碰不到 PostgreSQL 的上限 | 子块规模逼近单机内存上限、需要同义词纠错等高级全文能力、检索要独立扩缩容 |
| 独立向量库 | 过滤条件复杂且和向量检索强耦合，放在同一个库里一条 SQL 就能解决 | 向量规模超过单机内存、读写互相干扰、需要跨地域部署 |
| MySQL | 混合召回那条 SQL 用到六项特性，MySQL 缺四项（向量近似索引、数组重叠、部分索引、`FULL OUTER JOIN`） | — |
| 数据库端中文分词扩展 | 应用层 jieba 分词，保住两容器档位 | 长尾查询的全文召回明显不够，且能承担自建镜像的运维 |
| 重型解析服务 / 版式模型 | C 档只给协议，不装模型 | B 档解析质量不够时 |
| IVFFlat 索引 | 要先聚类，数据分布一变就得重建；HNSW 可以逐条增量写入 | — |
| 表分区 | 教学场景是多个小集合；分区适合少数大集合，而且管不到权限过滤 | 少数大集合，过滤条件主要按集合 |
| 查询时联网核验 | 官网慢起来要好几秒，还可能改版、挂掉，守不住延迟预算 | — |

---

## 许可与语料

代码部分请按仓库根目录的 LICENSE 文件为准。

语料不随代码分发：`corpus/` 下的内容由抓取脚本从中文维基导游获取（CC BY-SA，署名和相同方式共享是强制要求，来源链接与许可证记在 `corpus/_manifest/` 里，并随文档写进 `documents.meta`）。企业制度与内部笔记是手写的演示文件。`official/` 下的官方数据是演示值，不是真实票价，使用前请自行核实。