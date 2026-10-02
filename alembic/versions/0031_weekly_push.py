"""weekly push: a Sunday time on the user, and 'week' as a kind of push

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-01

ADR 0070. Additive, and nothing is sent until the user sets a time:

- users.weekly_push_at: the local time the week's summary arrives on Sunday, or NULL.
- pushes.kind may now be 'week' as well as 'morning'. The ledger's at-most-once claim
  (unique per user, kind and local date) already covers it: one per Sunday.

Downgrade deletes the week pushes (the old CHECK would refuse them), puts the old
CHECK back and drops the column.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column("users", sa.Column("weekly_push_at", sa.Time(), nullable=True))
	op.drop_constraint("ck_pushes_kind", "pushes", type_="check")
	op.create_check_constraint(
		"ck_pushes_kind", "pushes", "kind IN ('morning', 'week')"
	)


def downgrade() -> None:
	op.execute("DELETE FROM pushes WHERE kind = 'week'")
	op.drop_constraint("ck_pushes_kind", "pushes", type_="check")
	op.create_check_constraint("ck_pushes_kind", "pushes", "kind IN ('morning')")
	op.drop_column("users", "weekly_push_at")
