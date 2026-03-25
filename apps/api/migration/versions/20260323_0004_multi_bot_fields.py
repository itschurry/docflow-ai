"""add multi-bot speaker fields

Revision ID: 20260323_0004
Revises: 20260323_0003
Create Date: 2026-03-23 07:50:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260323_0004"
down_revision: Union[str, Sequence[str], None] = "20260323_0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # conversations: reply chain state
    op.add_column("conversations", sa.Column(
        "last_message_ids", sa.JSON(), nullable=False, server_default="{}"))

    # conv_messages: multi-bot identity fields
    op.add_column("conv_messages", sa.Column(
        "speaker_role", sa.String(100), nullable=True))
    op.add_column("conv_messages", sa.Column(
        "speaker_identity", sa.String(100), nullable=True))
    op.add_column("conv_messages", sa.Column(
        "speaker_bot_username", sa.String(100), nullable=True))
    op.add_column("conv_messages", sa.Column(
        "is_agent_message", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # agent_runs: speaker identity + telegram output message id
    op.add_column("agent_runs", sa.Column(
        "speaker_identity", sa.String(100), nullable=True))
    op.add_column("agent_runs", sa.Column(
        "output_message_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agent_runs", "output_message_id")
    op.drop_column("agent_runs", "speaker_identity")
    op.drop_column("conv_messages", "is_agent_message")
    op.drop_column("conv_messages", "speaker_bot_username")
    op.drop_column("conv_messages", "speaker_identity")
    op.drop_column("conv_messages", "speaker_role")
    op.drop_column("conversations", "last_message_ids")
