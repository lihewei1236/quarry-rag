"""core tables

Revision ID: 0001
Revises: 
Create Date: 2026-09-22 14:53:18.840581

"""
from typing import Sequence, Union
from pathlib import Path 
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).parent / "0001_core_tables.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS chunks, documents, collections, tenants CASCADE"
    )
