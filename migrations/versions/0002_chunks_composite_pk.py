"""chunks composite pk

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-23 11:04:39.694468

"""
from typing import Sequence, Union
from pathlib import Path 
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).parent / "0002_chunks_composite_pk.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    # 回退的前提是 chunk_id 在全表唯一。如果已经有两个集合存了同一个 chunk_id，
    # 回退会失败：这是正确的行为。回退会丢掉"两个集合各有一份"这个事实，
    # 数据库拒绝是在保护你。
    op.get_bind().exec_driver_sql(
        "ALTER TABLE chunks DROP CONSTRAINT chunks_pkey;"
        "ALTER TABLE chunks ADD CONSTRAINT chunks_pkey PRIMARY KEY (chunk_id);"
    )
