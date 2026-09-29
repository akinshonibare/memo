"""users.auto_accept, and the derivation status 'accepted'

Revision ID: 0013
Revises: 0012
Create Date: 2026-09-29

ADR 0030. A setting to keep what extraction finds without being asked to confirm it,
off by default. What it keeps is stored as 'accepted', not 'confirmed': confirmed means
a person checked it, and two things depend on that being true - Ask trusts a confirmed
date over the note's own words, and the eval harvester turns confirmed items into golden
labels. Stored as confirmed, the model's own guesses would become the answers it is
graded against.

NOT NULL with a server default: every existing user gets false in the same statement,
so there is no backfill and no window where the column is NULL.

Downgrade turns accepted rows back into pending_review, which is what they are.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
	op.add_column(
		"users",
		sa.Column(
			"auto_accept", sa.Boolean(), nullable=False, server_default=sa.false()
		),
	)
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_status")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_status CHECK (status IN "
		"('pending_review', 'confirmed', 'rejected', 'user_created', 'accepted'))"
	)


def downgrade() -> None:
	op.execute(
		"UPDATE derivations SET status = 'pending_review' WHERE status = 'accepted'"
	)
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_status")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_status CHECK (status IN "
		"('pending_review', 'confirmed', 'rejected', 'user_created'))"
	)
	op.drop_column("users", "auto_accept")
