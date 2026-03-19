"""add ops api keys

Revision ID: 20260319_0002
Revises: 20260319_0001
Create Date: 2026-03-19 00:20:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "20260319_0002"
down_revision: Union[str, Sequence[str], None] = "20260319_0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ops_api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key_id", sa.String(length=100), nullable=False),
        sa.Column("secret_hash", sa.String(length=128), nullable=False),
        sa.Column("role", sa.String(length=50),
                  nullable=False, server_default="ops"),
        sa.Column("is_active", sa.Boolean(), nullable=False,
                  server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_id"),
    )


def downgrade() -> None:
    op.drop_table("ops_api_keys")
