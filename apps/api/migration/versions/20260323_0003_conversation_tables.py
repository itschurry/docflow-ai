"""add conversation tables

Revision ID: 20260323_0003
Revises: 20260319_0002
Create Date: 2026-03-23 05:00:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260323_0003"
down_revision: Union[str, Sequence[str], None] = "20260319_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("platform", sa.String(length=30), nullable=False, server_default="telegram"),
        sa.Column("chat_id", sa.String(length=100), nullable=False),
        sa.Column("topic_id", sa.String(length=100), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=30), nullable=False, server_default="pipeline"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conversations_chat_id", "conversations", ["chat_id"])

    op.create_table(
        "participants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("handle", sa.String(length=100), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "conv_messages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("participant_id", sa.Uuid(), nullable=True),
        sa.Column("telegram_message_id", sa.Integer(), nullable=True),
        sa.Column("reply_to_message_id", sa.Integer(), nullable=True),
        sa.Column("raw_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("rendered_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("message_type", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["participants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_conv_messages_conversation_id", "conv_messages", ["conversation_id"])

    op.create_table(
        "mentions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("target_participant_id", sa.Uuid(), nullable=True),
        sa.Column("mention_text", sa.String(length=100), nullable=False),
        sa.ForeignKeyConstraint(["message_id"], ["conv_messages.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["target_participant_id"], ["participants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "agent_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_handle", sa.String(length=100), nullable=False),
        sa.Column("trigger_message_id", sa.Uuid(), nullable=True),
        sa.Column("input_snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("output_snapshot", sa.Text(), nullable=False, server_default=""),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="queued"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["trigger_message_id"], ["conv_messages.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_runs_conversation_id", "agent_runs", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("agent_runs")
    op.drop_table("mentions")
    op.drop_table("conv_messages")
    op.drop_table("participants")
    op.drop_table("conversations")
