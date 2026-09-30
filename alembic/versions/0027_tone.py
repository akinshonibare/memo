"""tone: how memo writes for each person

Revision ID: 0027
Revises: 0026
Create Date: 2026-09-30

ADR 0061. Additive: `users.tone`, one of plain, warm, brief, playful; plain for
everyone until they choose (a server default and a CHECK). It shapes Ask's answers,
highlight summaries and photo descriptions, never titles.

Downgrade drops the column; everyone is back to the one voice memo had.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column(
		"users",
		sa.Column("tone", sa.String(16), nullable=False, server_default="plain"),
	)
	op.create_check_constraint(
		"ck_users_tone", "users", "tone IN ('plain', 'warm', 'brief', 'playful')"
	)


def downgrade() -> None:
	op.drop_constraint("ck_users_tone", "users", type_="check")
	op.drop_column("users", "tone")
