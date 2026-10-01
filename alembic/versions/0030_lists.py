"""lists, and derivations.list_id: the things a note puts on a named list

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-01

ADR 0069. `lists` is an entity outside the capture -> derivation shape, like `people`
(ADR 0029): a list outlives any one note, and `list_item` derivations point at it.

Tenant isolation is enforced by the data layer twice, as for every table (ADR 0010):
RLS on `lists`, and a composite foreign key (list_id, user_id) -> lists (id, user_id),
so an item can only ever sit on its own user's list. Deleting a list deletes its items
(CASCADE), unlike a person's mentions: an item has no meaning without its list.

A CHECK keeps list_id on list items only, and a list item always has one.

Downgrade deletes every list item and the lists: there is nothing to keep them for.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"
KINDS_BEFORE = (
	"'todo', 'event', 'idea', 'mention', 'prayer', 'passage', 'reading', 'activity', "
	"'update', 'mood', 'mood_day'"
)


def upgrade() -> None:
	op.create_table(
		"lists",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("name", sa.String(80), nullable=False),
		# The name as matched: case, accents and "list" itself don't make a new list.
		sa.Column("key", sa.String(80), nullable=False),
		sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
		sa.UniqueConstraint("id", "user_id", name="uq_lists_id_user"),
		sa.UniqueConstraint("user_id", "key", name="uq_lists_user_key"),
		sa.CheckConstraint("length(btrim(name)) > 0", name="ck_lists_name"),
	)
	op.create_index("ix_lists_user", "lists", ["user_id"])
	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON lists TO {APP_ROLE}")
	op.execute("ALTER TABLE lists ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY lists_tenant ON lists USING {predicate} WITH CHECK {predicate}"
	)

	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		f"CHECK (kind IN ({KINDS_BEFORE}, 'list_item'))"
	)
	op.add_column("derivations", sa.Column("list_id", sa.Uuid(), nullable=True))
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT fk_derivations_list_same_user "
		"FOREIGN KEY (list_id, user_id) REFERENCES lists (id, user_id) "
		"ON DELETE CASCADE"
	)
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_list_only_on_items "
		"CHECK ((list_id IS NULL) = (kind <> 'list_item'))"
	)
	op.create_index(
		"ix_derivations_user_list",
		"derivations",
		["user_id", "list_id"],
		postgresql_where=sa.text("list_id IS NOT NULL"),
	)


def downgrade() -> None:
	op.drop_index("ix_derivations_user_list", table_name="derivations")
	op.execute("DELETE FROM derivations WHERE kind = 'list_item'")
	op.execute(
		"ALTER TABLE derivations DROP CONSTRAINT ck_derivations_list_only_on_items"
	)
	op.execute("ALTER TABLE derivations DROP CONSTRAINT fk_derivations_list_same_user")
	op.drop_column("derivations", "list_id")
	op.execute("ALTER TABLE derivations DROP CONSTRAINT ck_derivations_kind")
	op.execute(
		"ALTER TABLE derivations ADD CONSTRAINT ck_derivations_kind "
		f"CHECK (kind IN ({KINDS_BEFORE}))"
	)
	op.execute("DROP POLICY IF EXISTS lists_tenant ON lists")
	op.drop_index("ix_lists_user", table_name="lists")
	op.drop_table("lists")
