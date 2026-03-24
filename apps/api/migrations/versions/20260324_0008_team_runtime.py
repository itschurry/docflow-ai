"""add team runtime tables

Revision ID: 20260324_0008
Revises: 20260323_0007
Create Date: 2026-03-24 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_0008"
down_revision = "20260323_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "team_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("mode", sa.String(length=30), nullable=False, server_default="team-autonomous"),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
        sa.Column("requested_by", sa.String(length=100), nullable=False, server_default="web_user"),
        sa.Column("request_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("selected_agents", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("final_artifact_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["final_artifact_id"], ["conversation_artifacts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "team_tasks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_run_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("owner_handle", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="todo"),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("artifact_goal", sa.String(length=50), nullable=False, server_default=""),
        sa.Column("parent_task_id", sa.Uuid(), nullable=True),
        sa.Column("created_by_handle", sa.String(length=100), nullable=True),
        sa.Column("review_required", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["parent_task_id"], ["team_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_run_id"], ["team_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "team_task_dependencies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_task_id", sa.Uuid(), nullable=False),
        sa.Column("depends_on_task_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["depends_on_task_id"], ["team_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["team_task_id"], ["team_tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "team_activity_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_run_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=50), nullable=False),
        sa.Column("actor_handle", sa.String(length=100), nullable=True),
        sa.Column("target_handle", sa.String(length=100), nullable=True),
        sa.Column("summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["team_tasks.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["team_run_id"], ["team_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.add_column("conversation_artifacts", sa.Column("task_id", sa.Uuid(), nullable=True))
    if bind.dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_conversation_artifacts_task_id",
            "conversation_artifacts",
            "team_tasks",
            ["task_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "sqlite":
        op.drop_constraint("fk_conversation_artifacts_task_id", "conversation_artifacts", type_="foreignkey")
    op.drop_column("conversation_artifacts", "task_id")
    op.drop_table("team_activity_events")
    op.drop_table("team_task_dependencies")
    op.drop_table("team_tasks")
    op.drop_table("team_runs")
