"""add rag_config to team_runs

Revision ID: 20260324_0014
Revises: 20260324_0013
Create Date: 2026-03-24 20:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260324_0014"
down_revision = "20260324_0013"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if not has_column("team_runs", "rag_config"):
        op.add_column(
            "team_runs",
            sa.Column("rag_config", sa.JSON(), nullable=True),
        )


def downgrade() -> None:
    if has_column("team_runs", "rag_config"):
        op.drop_column("team_runs", "rag_config")
