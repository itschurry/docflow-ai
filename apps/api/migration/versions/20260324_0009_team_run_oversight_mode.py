"""add oversight mode to team runs

Revision ID: 20260324_0009
Revises: 20260324_0008
Create Date: 2026-03-24 00:30:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_0009"
down_revision = "20260324_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "team_runs",
        sa.Column("oversight_mode", sa.String(length=20), nullable=False, server_default="auto"),
    )


def downgrade() -> None:
    op.drop_column("team_runs", "oversight_mode")
