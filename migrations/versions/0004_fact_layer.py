"""fact layer

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-29 23:28:42.194474

"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).parent / "0004_fact_layer.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS fact_extractions;"
        "DROP TABLE IF EXISTS fact_mentions;"
        "DROP TABLE IF EXISTS facts;"
        "ALTER TABLE collections DROP COLUMN IF EXISTS facts_enabled;"
    )
