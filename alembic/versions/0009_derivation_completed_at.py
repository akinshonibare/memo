"""derivations.completed_at: a todo can be done

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-28

The Planner is a todo list, and a todo list needs a checkbox. Done is a timestamp
rather than a status: status records the review (is this item real?), completion
records what happened to it afterwards, and a confirmed todo that is done is still
confirmed. Nullable, no default, no backfill - nothing is done yet.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column(
		"derivations",
		sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
	)


def downgrade() -> None:
	op.drop_column("derivations", "completed_at")
