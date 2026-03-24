"""add output_type and document_provider to team_runs

Revision ID: 20260324_0012
Revises: 20260324_0011
Create Date: 2026-03-24 15:55:00.000000
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260324_0012"
down_revision = "20260324_0011"
branch_labels = None
depends_on = None


def has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = inspect(bind)
    return any(col["name"] == column_name for col in inspector.get_columns(table_name))


def upgrade() -> None:
    if not has_column("team_runs", "output_type"):
        op.add_column(
            "team_runs",
            sa.Column("output_type", sa.String(length=20), nullable=False, server_default="docx"),
        )
    if not has_column("team_runs", "document_provider"):
        op.add_column(
            "team_runs",
            sa.Column("document_provider", sa.String(length=30), nullable=False, server_default="internal_fallback"),
        )


def downgrade() -> None:
    if has_column("team_runs", "document_provider"):
        op.drop_column("team_runs", "document_provider")
    if has_column("team_runs", "output_type"):
        op.drop_column("team_runs", "output_type")
