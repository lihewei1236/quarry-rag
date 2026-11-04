"""全局配置。所有环境变量在这里收口，业务代码不直接读 os.environ。"""
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # ---------- 数据库 ----------
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "quarry"
    postgres_user: str = "quarry"
    postgres_password: SecretStr        # 没有默认值，不配就在启动时报错

    pool_min_size: int = 4
    pool_max_size: int = 20

    # ---------- Redis ----------
    redis_host: str = "localhost"
    redis_port: int = 6380
    redis_db: int = 0

    # ---------- 模型档位 ----------
    llm_base_url: str
    llm_api_key: SecretStr
    model_embed: str
    embed_dim: int = 1024
    embed_backend: Literal["api", "fake"] = "api"

    # ---------- 重排（第五章） ----------
    rerank_backend: Literal["api", "noop"] = "noop"
    rerank_model: str = ""
    rerank_url: str = ""
    rerank_api_key: SecretStr = SecretStr("")      # 留空 = 复用 LLM_API_KEY
    rerank_candidates: int = Field(default=30, ge=1, le=100)
    rerank_timeout_s: float = Field(default=3.0, gt=0)

    # ---------- 上下文（第五章） ----------
    context_max_chars: int = Field(default=6000, ge=500, le=50000)
    neighbor_radius: int = Field(default=1, ge=0, le=3)

    # ---------- 应用 ----------
    app_env: Literal["development", "production"] = "development"
    log_level: str = "INFO"
    default_top_k: int = Field(default=8, ge=1, le=50)

    # 第 5～8 章会在这里依次追加：重排、上下文、事实层、评测、对外接口

    # ---------- 事实层（第六章） ----------
    fact_model: str = ""
    fact_base_url: str = ""
    fact_api_key: SecretStr = SecretStr("")        # 留空 = 复用 LLM_API_KEY
    fact_concurrency: int = Field(default=4, ge=1, le=16)
    fact_timeout_s: float = Field(default=30.0, gt=0)
    fact_ttl_days: int = Field(default=90, ge=1, le=3650)

    # ---------- 评测（第七章） ----------
    judge_model: str = ""
    judge_base_url: str = ""
    judge_api_key: SecretStr = SecretStr("")       # 留空 = 复用 LLM_API_KEY
    judge_timeout_s: float = Field(default=60.0, gt=0)
    eval_top_k: int = Field(default=5, ge=1, le=20)

    # ---------- 对外接口（第八章） ----------
    api_request_timeout_s: float = Field(default=5.0, gt=0)
    api_max_top_k: int = Field(default=20, ge=1, le=50)
    api_log_queries: bool = True

    @property
    def dsn(self) -> str:
        """asyncpg 用的连接串。"""
        pw = self.postgres_password.get_secret_value()
        return (f"postgresql://{self.postgres_user}:{pw}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")

    @property
    def alembic_url(self) -> str:
        """Alembic 用的连接串，驱动换成同步的 psycopg2。"""
        pw = self.postgres_password.get_secret_value()
        return (f"postgresql+psycopg2://{self.postgres_user}:{pw}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def switches(self) -> dict[str, object]:
        """给 /healthz 回显用。只返回开关与参数，不返回地址和密钥。"""
        return {
            "embed_backend": self.embed_backend,
            "embed_model": self.model_embed,
            "embed_dim": self.embed_dim,
            "rerank_backend": self.rerank_backend,
            "rerank_model": self.rerank_model if self.rerank_backend == "api" else None,
            "rerank_candidates": self.rerank_candidates,
            "context_max_chars": self.context_max_chars,
            "neighbor_radius": self.neighbor_radius,
            "default_top_k": self.default_top_k,
            "fact_model": self.fact_model or None,
            "fact_ttl_days": self.fact_ttl_days,
        }


@lru_cache
def get_settings() -> Settings:
    """全进程只解析一次。测试里要重新加载就调 get_settings.cache_clear()。"""
    return Settings()