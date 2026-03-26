"""add review_mode to team_runs

Revision ID: 20260326_0016
Revises: 20260326_0015
Create Date: 2026-03-26 04:00:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260326_0016"
down_revision = "20260326_0015"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if not has_column("team_runs", "review_mode"):
        op.add_column(
            "team_runs",
            sa.Column("review_mode", sa.String(20), nullable=False, server_default="balanced"),
        )


def downgrade() -> None:
    if has_column("team_runs", "review_mode"):
        op.drop_column("team_runs", "review_mode")
