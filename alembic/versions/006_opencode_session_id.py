"""Separate OpenCode session id from Cursor chat id

Revision ID: 006
Revises: 005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_sessions",
        sa.Column("opencode_session_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_sessions", "opencode_session_id")
