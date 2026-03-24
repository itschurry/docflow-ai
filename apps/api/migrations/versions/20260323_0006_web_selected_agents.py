"""add selected_agents for web workspace conversations

Revision ID: 20260323_0006
Revises: 20260323_0005
Create Date: 2026-03-23 12:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260323_0006"
down_revision: Union[str, Sequence[str], None] = "20260323_0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("selected_agents", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "selected_agents")

