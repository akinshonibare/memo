"""faith: a space for notes, prayers and passages, and a reading plan's progress

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-29

ADR 0042. All additive, and nothing changes for anyone until they turn Faith on:

- users.faith_enabled (default false) and users.bible_translation (default 'NIV').
  On the account, not the phone: the server hides faith notes from Home only while
  Faith is on, so it has to know.
- captures.space and chunks.space, 'main' or 'faith', both defaulting to 'main', so
  every existing row is where it always was. The chunk copy is what lets search and
  Ask leave faith notes out by filtering the chunks table alone, as they already do
  for user_id (ADR 0010).
- derivation kinds 'prayer' and 'passage'. A prayer may name a person, so the check
  that kept person_id on mentions only becomes a check on the kinds that may.
- faith_readings: one row per reading marked read. The plan's position is derived
  from it, which is also what the weekly review reads. RLS like every user table.

Downgrade deletes prayers, passages and readings (without the columns they have no
meaning) and moves faith notes back to the main space by dropping the column; the
notes themselves are untouched.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"
TRANSLATIONS = "'NIV', 'ESV', 'KJV', 'NKJV', 'NLT', 'CSB', 'NASB', 'MSG'"


def upgrade() -> None:
	op.add_column(
		"users",
		sa.Column(
			"faith_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
		),
	)
	op.add_column(
		"users",
		sa.Column(
			"bible_translation", sa.String(8), nullable=False, server_default="NIV"
		),
	)
	op.execute(
		"ALTER TABLE users ADD CONSTRAINT ck_users_bible_translation "
		f"CHECK (bible_translation IN ({TRANSLATIONS}))"
	)

	for table in ("captures", "chunks"):
		op.add_column(
			table,
			sa.Column("space", sa.String(8), nullable=False, server_default="main"),
		)
		op.execute(
			f"ALTER TABLE {table} ADD CONSTRAINT ck_{table}_space "
			"CHECK (space IN ('main', 'faith'))"
		)

	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		"CHECK (kind IN ('todo', 'event', 'idea', 'mention', 'prayer', 'passage'))"
	)
	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT ck_derivations_person_only_on_mentions"
	)
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_person_kinds "
		"CHECK (person_id IS NULL OR kind IN ('mention', 'prayer'))"
	)

	op.create_table(
		"faith_readings",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("plan", sa.String(32), nullable=False),
		sa.Column("cycle", sa.Integer(), nullable=False, server_default="1"),
		sa.Column("position", sa.Integer(), nullable=False),
		sa.Column(
			"read_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.UniqueConstraint(
			"user_id", "plan", "cycle", "position", name="uq_faith_readings_position"
		),
		sa.CheckConstraint("position >= 0", name="ck_faith_readings_position"),
		sa.CheckConstraint("cycle >= 1", name="ck_faith_readings_cycle"),
	)
	op.create_index(
		"ix_faith_readings_user_read", "faith_readings", ["user_id", "read_at"]
	)
	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON faith_readings TO {APP_ROLE}")
	op.execute("ALTER TABLE faith_readings ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY faith_readings_tenant ON faith_readings "
		f"USING {predicate} WITH CHECK {predicate}"
	)


def downgrade() -> None:
	op.drop_table("faith_readings")
	# Their chunks (and any citation of those) cascade with them.
	op.execute("DELETE FROM derivations WHERE kind IN ('prayer', 'passage')")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_person_kinds")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_person_only_on_mentions "
		"CHECK (person_id IS NULL OR kind = 'mention')"
	)
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		"CHECK (kind IN ('todo', 'event', 'idea', 'mention'))"
	)
	for table in ("chunks", "captures"):
		op.drop_column(table, "space")
	op.drop_column("users", "bible_translation")
	op.drop_column("users", "faith_enabled")
