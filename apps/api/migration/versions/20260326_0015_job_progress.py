"""add progress to jobs

Revision ID: 20260326_0015
Revises: 20260324_0014
Create Date: 2026-03-26 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = "20260326_0015"
down_revision: Union[str, Sequence[str], None] = "20260324_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if not has_column("jobs", "progress"):
        op.add_column(
            "jobs",
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    if has_column("jobs", "progress"):
        op.drop_column("jobs", "progress")
