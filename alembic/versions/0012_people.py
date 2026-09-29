"""people, and derivations.person_id: who a note is about

Revision ID: 0012
Revises: 0011
Create Date: 2026-09-28

ADR 0029. `people` is the one entity ADR 0008 allows outside the capture -> derivation
shape: a person outlives any one note, and `mention` derivations point at them.

Tenant isolation is enforced by the data layer twice, as for every other table
(ADR 0010): RLS on `people`, and a composite foreign key (person_id, user_id) ->
people (id, user_id), so a mention can only ever point at its own user's person. That
key uses the column-list form of SET NULL (Postgres 15+), like migration 0008: a plain
SET NULL would null user_id as well and drop the mention out of its tenant. Deleting a
person (a merge, or the last mention rejected) leaves their mentions unlinked, which
the app shows as "who is this?" rather than losing them.

A CHECK keeps person_id on mentions only, so no other kind can quietly grow a link.

Downgrade deletes every mention: without `people` there is nothing for them to name.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
	op.create_table(
		"people",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("name", sa.String(120), nullable=False),
		sa.Column(
			"aliases",
			postgresql.ARRAY(sa.String(120)),
			nullable=False,
			server_default="{}",
		),
		sa.Column("note", sa.String(120), nullable=True),
		sa.Column("reach_out_every_days", sa.Integer(), nullable=True),
		sa.Column("snoozed_until", sa.Date(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.UniqueConstraint("id", "user_id", name="uq_people_id_user"),
		sa.CheckConstraint("length(btrim(name)) > 0", name="ck_people_name"),
		sa.CheckConstraint(
			"reach_out_every_days IS NULL OR reach_out_every_days BETWEEN 1 AND 365",
			name="ck_people_reach_out_every_days",
		),
	)
	op.create_index("ix_people_user", "people", ["user_id"])
	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON people TO {APP_ROLE}")
	op.execute("ALTER TABLE people ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY people_tenant ON people "
		f"USING {predicate} WITH CHECK {predicate}"
	)

	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		"CHECK (kind IN ('todo', 'event', 'idea', 'mention'))"
	)
	op.add_column("derivations", sa.Column("person_id", sa.Uuid(), nullable=True))
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT fk_derivations_person_same_user "
		"FOREIGN KEY (person_id, user_id) REFERENCES people (id, user_id) "
		"ON DELETE SET NULL (person_id)"
	)
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_person_only_on_mentions "
		"CHECK (person_id IS NULL OR kind = 'mention')"
	)
	op.create_index(
		"ix_derivations_user_person",
		"derivations",
		["user_id", "person_id"],
		postgresql_where=sa.text("person_id IS NOT NULL"),
	)


def downgrade() -> None:
	op.drop_index("ix_derivations_user_person", table_name="derivations")
	# Their chunks (and any citation of those) cascade with them.
	op.execute("DELETE FROM derivations WHERE kind = 'mention'")
	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT ck_derivations_person_only_on_mentions"
	)
	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT fk_derivations_person_same_user"
	)
	op.drop_column("derivations", "person_id")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		"CHECK (kind IN ('todo', 'event', 'idea'))"
	)
	op.drop_table("people")
