"""add document ir metadata fields

Revision ID: 20260324_0011
Revises: 20260324_0010
Create Date: 2026-03-24 15:10:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "20260324_0011"
down_revision = "20260324_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_column(table_name: str, column_name: str) -> bool:
        return column_name in {item["name"] for item in inspector.get_columns(table_name)}

    if not has_column("files", "document_type"):
        op.add_column(
            "files",
            sa.Column("document_type", sa.String(length=30), nullable=False, server_default=""),
        )
    if not has_column("files", "document_summary"):
        op.add_column(
            "files",
            sa.Column("document_summary", sa.Text(), nullable=False, server_default=""),
        )
    if not has_column("team_runs", "source_file_ids"):
        op.add_column(
            "team_runs",
            sa.Column("source_file_ids", sa.JSON(), nullable=False, server_default="[]"),
        )
    if not has_column("team_runs", "source_ir_summary"):
        op.add_column(
            "team_runs",
            sa.Column("source_ir_summary", sa.Text(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("team_runs", "source_ir_summary")
    op.drop_column("team_runs", "source_file_ids")
    op.drop_column("files", "document_summary")
    op.drop_column("files", "document_type")
