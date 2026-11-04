"""api keys and query logs

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-30 21:33:38.016965

"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0006'
down_revision: Union[str, Sequence[str], None] = '0005'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).parent / "0006_api.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP TABLE IF EXISTS query_logs;"
        "DROP TABLE IF EXISTS api_keys;"
    )
