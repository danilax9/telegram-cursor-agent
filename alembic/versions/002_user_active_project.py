"""Add active project to users

Revision ID: 002
Revises: 001
Create Date: 2026-09-19

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("active_project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_users_active_project_id_projects",
        "users",
        "projects",
        ["active_project_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_users_active_project_id_projects", "users", type_="foreignkey")
    op.drop_column("users", "active_project_id")
