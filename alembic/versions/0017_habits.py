"""habits: what you did, noticed in notes; habits you track; who is family

Revision ID: 0017
Revises: 0016
Create Date: 2026-09-29

ADR 0043. All additive, and nothing happens for anyone until they turn Habits on:

- users.habits_enabled (default false).
- people.family: NULL (memo guesses from the name or note), true or false (you said).
- derivation kind 'activity': something the author did, found in a note by the habits
  reader, or logged with a tap (no note). Counted towards habits; never an item.
- habits: one row per habit tracked, or per suggestion dismissed (status). Counts are
  computed from activities, People's contacts and Faith's readings, never stored.
  RLS like every user table.

Downgrade deletes activities and habits; notes, people and everything else stay.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
	op.add_column(
		"users",
		sa.Column(
			"habits_enabled", sa.Boolean(), nullable=False, server_default=sa.false()
		),
	)
	op.add_column("people", sa.Column("family", sa.Boolean(), nullable=True))

	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind CHECK (kind IN "
		"('todo', 'event', 'idea', 'mention', 'prayer', 'passage', 'activity'))"
	)

	op.create_table(
		"habits",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("key", sa.String(40), nullable=False),
		sa.Column("name", sa.String(80), nullable=False),
		sa.Column("source", sa.String(16), nullable=False),
		sa.Column("status", sa.String(16), nullable=False, server_default="active"),
		sa.Column("target_per_week", sa.Integer(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.UniqueConstraint("user_id", "key", name="uq_habits_user_key"),
		sa.CheckConstraint(
			"source IN ('activity', 'family', 'bible')", name="ck_habits_source"
		),
		sa.CheckConstraint(
			"status IN ('active', 'dismissed')", name="ck_habits_status"
		),
		sa.CheckConstraint(
			"target_per_week IS NULL OR target_per_week BETWEEN 1 AND 7",
			name="ck_habits_target",
		),
		sa.CheckConstraint("length(btrim(name)) > 0", name="ck_habits_name"),
	)
	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON habits TO {APP_ROLE}")
	op.execute("ALTER TABLE habits ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY habits_tenant ON habits "
		f"USING {predicate} WITH CHECK {predicate}"
	)
	# Activities are looked up by key and day for every habit on every read.
	op.execute(
		"CREATE INDEX ix_derivations_user_activity ON derivations "
		"(user_id, (payload->>'key'), (payload->>'occurred_on')) "
		"WHERE kind = 'activity'"
	)


def downgrade() -> None:
	op.execute("DROP INDEX ix_derivations_user_activity")
	op.drop_table("habits")
	op.execute("DELETE FROM derivations WHERE kind = 'activity'")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind CHECK (kind IN "
		"('todo', 'event', 'idea', 'mention', 'prayer', 'passage'))"
	)
	op.drop_column("people", "family")
	op.drop_column("users", "habits_enabled")
