"""add dynamic handoff runtime fields

Revision ID: 20260323_0005
Revises: 20260323_0004
Create Date: 2026-03-23 09:35:00
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260323_0005"
down_revision: Union[str, Sequence[str], None] = "20260323_0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # conversations
    op.add_column("conversations", sa.Column("turn_limit", sa.Integer(), nullable=False, server_default="8"))
    op.add_column("conversations", sa.Column("is_waiting_user", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conversations", sa.Column("autonomy_level", sa.String(length=30), nullable=False, server_default="autonomous-lite"))
    op.add_column("conversations", sa.Column("current_agent", sa.String(length=100), nullable=True))
    op.add_column("conversations", sa.Column("current_identity", sa.String(length=100), nullable=True))
    op.add_column("conversations", sa.Column("suggested_next_agent", sa.String(length=100), nullable=True))
    op.add_column("conversations", sa.Column("approved_next_agent", sa.String(length=100), nullable=True))
    op.add_column("conversations", sa.Column("last_handoff_reason", sa.Text(), nullable=True))
    op.add_column("conversations", sa.Column("task_status", sa.String(length=120), nullable=True))
    op.add_column("conversations", sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conversations", sa.Column("needs_user_input", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conversations", sa.Column("loop_guard_counter", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("conversations", sa.Column("last_n_agents", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("conversations", sa.Column("anchor_message_id", sa.Integer(), nullable=True))
    op.add_column("conversations", sa.Column("last_message_id", sa.Integer(), nullable=True))
    op.add_column("conversations", sa.Column("last_user_goal_snapshot", sa.Text(), nullable=False, server_default=""))
    op.add_column("conversations", sa.Column("completion_score", sa.Integer(), nullable=True))
    op.add_column("conversations", sa.Column("confidence_trend", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("conversations", sa.Column("artifact_requested", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conversations", sa.Column("export_ready", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # messages
    op.add_column("conv_messages", sa.Column("visible_message", sa.Text(), nullable=True))
    op.add_column("conv_messages", sa.Column("suggested_next_agent", sa.String(length=100), nullable=True))
    op.add_column("conv_messages", sa.Column("approved_next_agent", sa.String(length=100), nullable=True))
    op.add_column("conv_messages", sa.Column("handoff_reason", sa.Text(), nullable=True))
    op.add_column("conv_messages", sa.Column("task_status", sa.String(length=120), nullable=True))
    op.add_column("conv_messages", sa.Column("done", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conv_messages", sa.Column("needs_user_input", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("conv_messages", sa.Column("is_progress_turn", sa.Boolean(), nullable=False, server_default=sa.text("false")))

    # agent_runs
    op.add_column("agent_runs", sa.Column("input_context_snapshot", sa.Text(), nullable=False, server_default=""))
    op.add_column("agent_runs", sa.Column("suggested_next_agent", sa.String(length=100), nullable=True))
    op.add_column("agent_runs", sa.Column("approved_next_agent", sa.String(length=100), nullable=True))
    op.add_column("agent_runs", sa.Column("handoff_reason", sa.Text(), nullable=True))
    op.add_column("agent_runs", sa.Column("validation_result", sa.JSON(), nullable=True))
    op.add_column("agent_runs", sa.Column("fallback_applied", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("agent_runs", sa.Column("progress_detected", sa.Boolean(), nullable=False, server_default=sa.text("false")))
    op.add_column("agent_runs", sa.Column("termination_reason", sa.String(length=120), nullable=True))

    # new default mode
    op.execute("UPDATE conversations SET mode='autonomous-lite' WHERE mode='pipeline'")


def downgrade() -> None:
    op.drop_column("agent_runs", "termination_reason")
    op.drop_column("agent_runs", "progress_detected")
    op.drop_column("agent_runs", "fallback_applied")
    op.drop_column("agent_runs", "validation_result")
    op.drop_column("agent_runs", "handoff_reason")
    op.drop_column("agent_runs", "approved_next_agent")
    op.drop_column("agent_runs", "suggested_next_agent")
    op.drop_column("agent_runs", "input_context_snapshot")

    op.drop_column("conv_messages", "is_progress_turn")
    op.drop_column("conv_messages", "needs_user_input")
    op.drop_column("conv_messages", "done")
    op.drop_column("conv_messages", "task_status")
    op.drop_column("conv_messages", "handoff_reason")
    op.drop_column("conv_messages", "approved_next_agent")
    op.drop_column("conv_messages", "suggested_next_agent")
    op.drop_column("conv_messages", "visible_message")

    op.drop_column("conversations", "export_ready")
    op.drop_column("conversations", "artifact_requested")
    op.drop_column("conversations", "confidence_trend")
    op.drop_column("conversations", "completion_score")
    op.drop_column("conversations", "last_user_goal_snapshot")
    op.drop_column("conversations", "last_message_id")
    op.drop_column("conversations", "anchor_message_id")
    op.drop_column("conversations", "last_n_agents")
    op.drop_column("conversations", "loop_guard_counter")
    op.drop_column("conversations", "needs_user_input")
    op.drop_column("conversations", "done")
    op.drop_column("conversations", "task_status")
    op.drop_column("conversations", "last_handoff_reason")
    op.drop_column("conversations", "approved_next_agent")
    op.drop_column("conversations", "suggested_next_agent")
    op.drop_column("conversations", "current_identity")
    op.drop_column("conversations", "current_agent")
    op.drop_column("conversations", "autonomy_level")
    op.drop_column("conversations", "is_waiting_user")
    op.drop_column("conversations", "turn_limit")
