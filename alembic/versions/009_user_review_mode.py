"""Replace review flag with off/on/max mode

Revision ID: 009
Revises: 008
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "009"
down_revision: str | None = "008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "review_mode",
            sa.String(length=8),
            nullable=False,
            server_default="off",
        ),
    )
    op.execute(
        "UPDATE users SET review_mode = 'on' WHERE review_before_answer IS TRUE"
    )
    op.drop_column("users", "review_before_answer")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "review_before_answer",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.execute(
        "UPDATE users SET review_before_answer = TRUE WHERE review_mode <> 'off'"
    )
    op.drop_column("users", "review_mode")
