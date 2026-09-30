"""mood: how you sounded, and your own word for a day

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-30

ADR 0058. Additive:

- derivation kind 'mood': memo's read of one note (how the author sounded), always of a
  note. Goes with the note, like everything taken from it (ADR 0053).
- derivation kind 'mood_day': your own word for how a day was, never of a note. One per
  day per person (a partial unique index); saying again replaces it.

Downgrade deletes both kinds. Your words for days go with them: they only existed to
correct reads that are also gone.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

KINDS_BEFORE = (
	"'todo', 'event', 'idea', 'mention', 'prayer', 'passage', 'reading', 'activity', "
	"'update'"
)


def upgrade() -> None:
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind CHECK (kind IN "
		f"({KINDS_BEFORE}, 'mood', 'mood_day'))"
	)
	op.create_check_constraint(
		"ck_derivations_mood_capture",
		"derivations",
		"(kind <> 'mood' OR capture_id IS NOT NULL)"
		" AND (kind <> 'mood_day' OR capture_id IS NULL)",
	)
	op.create_index(
		"uq_derivations_mood_day",
		"derivations",
		["user_id", sa.text("(payload ->> 'day')")],
		unique=True,
		postgresql_where=sa.text("kind = 'mood_day'"),
	)


def downgrade() -> None:
	op.drop_index("uq_derivations_mood_day", table_name="derivations")
	op.execute("DELETE FROM derivations WHERE kind IN ('mood', 'mood_day')")
	op.drop_constraint("ck_derivations_mood_capture", "derivations", type_="check")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind CHECK (kind IN "
		f"({KINDS_BEFORE}))"
	)
