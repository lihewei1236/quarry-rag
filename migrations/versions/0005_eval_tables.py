"""eval tables

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-30 14:23:08.844520

"""
from typing import Sequence, Union
from pathlib import Path
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).parent / "0005_eval.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS eval_results;"
        "DROP TABLE IF EXISTS eval_runs;"
        "DROP TABLE IF EXISTS eval_queries;"
    )
