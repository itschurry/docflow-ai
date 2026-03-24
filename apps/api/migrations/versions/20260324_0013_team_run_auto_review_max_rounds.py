"""add auto_review_max_rounds to team_runs

Revision ID: 20260324_0013
Revises: 20260324_0012
Create Date: 2026-03-24 19:45:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260324_0013"
down_revision = "20260324_0012"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if not has_column("team_runs", "auto_review_max_rounds"):
        op.add_column(
            "team_runs",
            sa.Column("auto_review_max_rounds", sa.Integer(), nullable=False, server_default="2"),
        )


def downgrade() -> None:
    if has_column("team_runs", "auto_review_max_rounds"):
        op.drop_column("team_runs", "auto_review_max_rounds")

