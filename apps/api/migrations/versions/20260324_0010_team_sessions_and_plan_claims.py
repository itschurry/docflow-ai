"""add team sessions, inbox, plan status, and task claims

Revision ID: 20260324_0010
Revises: 20260324_0009
Create Date: 2026-03-24 13:20:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_0010"
down_revision = "20260324_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    def has_column(table_name: str, column_name: str) -> bool:
        return column_name in {item["name"] for item in inspector.get_columns(table_name)}

    if not has_column("team_runs", "plan_status"):
        op.add_column(
            "team_runs",
            sa.Column("plan_status", sa.String(length=30), nullable=False, server_default="pending"),
        )

    if not has_column("team_tasks", "claim_status"):
        op.add_column(
            "team_tasks",
            sa.Column("claim_status", sa.String(length=20), nullable=False, server_default="open"),
        )
    if not has_column("team_tasks", "task_kind"):
        op.add_column(
            "team_tasks",
            sa.Column("task_kind", sa.String(length=30), nullable=False, server_default="draft"),
        )
    if not has_column("team_tasks", "claimed_by_session_id"):
        op.add_column("team_tasks", sa.Column("claimed_by_session_id", sa.Uuid(), nullable=True))
    if not has_column("team_tasks", "claim_expires_at"):
        op.add_column("team_tasks", sa.Column("claim_expires_at", sa.DateTime(), nullable=True))

    if "team_member_sessions" not in existing_tables:
        op.create_table(
            "team_member_sessions",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("team_run_id", sa.Uuid(), nullable=False),
            sa.Column("handle", sa.String(length=100), nullable=False),
            sa.Column("role", sa.String(length=30), nullable=False, server_default="worker"),
            sa.Column("display_name", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=30), nullable=False, server_default="idle"),
            sa.Column("current_task_id", sa.Uuid(), nullable=True),
            sa.Column("context_window_summary", sa.Text(), nullable=False, server_default=""),
            sa.Column("inbox_cursor", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["current_task_id"], ["team_tasks.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["team_run_id"], ["team_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    if "team_inbox_messages" not in existing_tables:
        op.create_table(
            "team_inbox_messages",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("team_run_id", sa.Uuid(), nullable=False),
            sa.Column("from_session_id", sa.Uuid(), nullable=True),
            sa.Column("to_session_id", sa.Uuid(), nullable=True),
            sa.Column("related_task_id", sa.Uuid(), nullable=True),
            sa.Column("message_type", sa.String(length=30), nullable=False, server_default="direct"),
            sa.Column("subject", sa.String(length=200), nullable=False, server_default=""),
            sa.Column("content", sa.Text(), nullable=False, server_default=""),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="unread"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["from_session_id"], ["team_member_sessions.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["related_task_id"], ["team_tasks.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["team_run_id"], ["team_runs.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["to_session_id"], ["team_member_sessions.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )

    if bind.dialect.name != "sqlite":
        existing_fks = {fk["name"] for fk in inspector.get_foreign_keys("team_tasks")}
        if "fk_team_tasks_claimed_by_session_id" not in existing_fks:
            op.create_foreign_key(
                "fk_team_tasks_claimed_by_session_id",
                "team_tasks",
                "team_member_sessions",
                ["claimed_by_session_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    bind = op.get_bind()

    if bind.dialect.name != "sqlite":
        op.drop_constraint("fk_team_tasks_claimed_by_session_id", "team_tasks", type_="foreignkey")

    op.drop_table("team_inbox_messages")
    op.drop_table("team_member_sessions")
    op.drop_column("team_tasks", "claim_expires_at")
    op.drop_column("team_tasks", "claimed_by_session_id")
    op.drop_column("team_tasks", "task_kind")
    op.drop_column("team_tasks", "claim_status")
    op.drop_column("team_runs", "plan_status")
