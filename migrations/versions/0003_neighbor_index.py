"""neighbor index

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29 15:31:11.405956

"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    sql = (Path(__file__).parent / "0003_neighbor_index.sql").read_text(encoding="utf-8")
    op.get_bind().exec_driver_sql(sql)


def downgrade() -> None:
    op.get_bind().exec_driver_sql(
        "DROP INDEX IF EXISTS chunks_neighbors;"
        "CREATE INDEX chunks_neighbors ON chunks (document_id, heading_path, ord)"
        " WHERE level = 0;"
    )