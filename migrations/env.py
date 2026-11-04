"""Alembic 运行环境。数据库地址从 quarry 的 settings 读，不写在 alembic.ini 里。"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from quarry.infra.settings import get_settings

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 本项目的 DDL 全部手写，不用 ORM 模型自动生成迁移，所以这里是 None
target_metadata = None


def run_migrations_offline() -> None:
    """只生成 SQL 不连库。alembic upgrade head --sql 会走这里。"""
    context.configure(
        url=get_settings().alembic_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """正常执行迁移。"""
    engine = create_engine(get_settings().alembic_url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()