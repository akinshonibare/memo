"""updates: a later note answers a prayer or carries on an idea

Revision ID: 0020
Revises: 0019
Create Date: 2026-09-29

ADR 0048. Additive:

- derivation kind 'update': a suggestion, from a note, that it changes an earlier item.
  Always pending until the author says yes or no.
- derivations.target_id: the item it would change. Same-user composite FK (ADR 0010),
  ON DELETE CASCADE: a suggestion about a deleted prayer or idea goes with it. Set on
  updates and only on updates (a CHECK).

Downgrade deletes updates and the column. A prayer already marked answered through one
stays answered: that was the author's decision, not the suggestion's.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_BEFORE = (
	"'todo', 'event', 'idea', 'mention', 'prayer', 'passage', 'reading', 'activity'"
)


def upgrade() -> None:
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind CHECK (kind IN "
		f"({KINDS_BEFORE}, 'update'))"
	)
	op.add_column("derivations", sa.Column("target_id", sa.Uuid(), nullable=True))
	op.create_foreign_key(
		"fk_derivations_target_same_user",
		"derivations",
		"derivations",
		["target_id", "user_id"],
		["id", "user_id"],
		ondelete="CASCADE",
	)
	op.create_check_constraint(
		"ck_derivations_target_update",
		"derivations",
		"(target_id IS NULL) = (kind <> 'update')",
	)
	op.create_index(
		"ix_derivations_target",
		"derivations",
		["user_id", "target_id"],
		postgresql_where=sa.text("target_id IS NOT NULL"),
	)


def downgrade() -> None:
	op.drop_index("ix_derivations_target", table_name="derivations")
	op.execute("DELETE FROM derivations WHERE kind = 'update'")
	op.drop_constraint("ck_derivations_target_update", "derivations", type_="check")
	op.drop_constraint(
		"fk_derivations_target_same_user", "derivations", type_="foreignkey"
	)
	op.drop_column("derivations", "target_id")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind CHECK (kind IN "
		f"({KINDS_BEFORE}))"
	)
