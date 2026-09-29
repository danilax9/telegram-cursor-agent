"""Mark each agent session as Cursor or OpenCode

Revision ID: 007
Revises: 006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("engine", sa.String(length=20), nullable=True),
    )
    op.execute("UPDATE agent_sessions SET engine = 'cursor' WHERE engine IS NULL")


def downgrade() -> None:
    op.drop_column("agent_sessions", "engine")
