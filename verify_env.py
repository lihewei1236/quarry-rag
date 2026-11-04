"""启动期自检。五项都是 ✅ 才算环境打通。"""
import asyncio
import sys

import asyncpg
import redis.asyncio as aioredis

from quarry.infra.settings import get_settings

MIN_PGVECTOR = (0, 8, 2)


def _parse(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".")[:3])


async def main() -> int:
    s = get_settings()
    print("配置加载：")
    print(f"   postgres = {s.postgres_host}:{s.postgres_port}/{s.postgres_db}")
    print(f"   redis    = {s.redis_host}:{s.redis_port}")
    print(f"   api_key  = {s.llm_api_key}")      # SecretStr 自动脱敏
    print(f"   embed    = {s.model_embed} / {s.embed_dim} 维 / 后端 {s.embed_backend}")
    print(f"   app_env  = {s.app_env}")
    print()

    ok = True

    # ① Postgres 连通
    try:
        conn = await asyncpg.connect(s.dsn, timeout=5)
    except Exception as e:
        print(f"❌ Postgres 连不上：{e!r}")
        print("   先跑 docker compose ps，确认 postgres 是 (healthy)；再检查 .env 里的端口和密码")
        return 1
    try:
        ver = await conn.fetchval("SELECT version()")
        print(f"✅ Postgres 连通：{ver[:45]}...")

        # ② pgvector 扩展版本
        ext = await conn.fetchval(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        if ext is None:
            print("❌ pgvector 扩展未安装。检查 docker/init-db.sql，"
                  "或者进 psql 手动执行 CREATE EXTENSION vector;")
            ok = False
        elif _parse(ext) < MIN_PGVECTOR:
            print(f"❌ pgvector {ext} 低于要求的 {'.'.join(map(str, MIN_PGVECTOR))}")
            ok = False
        else:
            print(f"✅ pgvector 版本：{ext}")

        # ③ 表是否建好
        if await conn.fetchval("SELECT to_regclass('chunks') IS NOT NULL"):
            print("✅ 迁移已跑：chunks 表存在")
        else:
            print("❌ chunks 表不存在。跑 uv run alembic upgrade head")
            ok = False
    finally:
        await conn.close()

    # ④ Redis 连通
    r = aioredis.from_url(s.redis_url, socket_connect_timeout=5)
    try:
        print(f"✅ Redis 连通：ping = {await r.ping()}")
    except Exception as e:
        print(f"❌ Redis 连不上：{e!r}")
        ok = False
    finally:
        await r.aclose()

    # ⑤ 向量化接口连通，且维度和配置一致
    from quarry.infra.embed import get_embedder
    try:
        vec = (await get_embedder().embed(["连通性测试"]))[0]
    except Exception as e:
        print(f"❌ 向量化接口调用失败：{e!r}")
        print("   检查 LLM_API_KEY、LLM_BASE_URL；没有 key 可以先把 EMBED_BACKEND 改成 fake")
        return 1
    if len(vec) != s.embed_dim:
        print(f"❌ 向量维度不一致：模型返回 {len(vec)}，配置是 {s.embed_dim}")
        ok = False
    elif s.embed_backend == "fake":
        print(f"⚠️  向量化用的是 fake 后端（{len(vec)} 维），检索结果没有语义意义。"
              f"第二章开始把 EMBED_BACKEND 改回 api。")
    else:
        print(f"✅ 向量化接口连通：{s.model_embed} 返回 {len(vec)} 维")

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))