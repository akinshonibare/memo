"""morning_views and resurfacings

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28

Hand-written. The morning view is built ahead of time (a push-side read path: the
brief's "retrieval is the payoff") and stored per local date; resurfacings record what
was shown so an idea is not shown again for a while.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"


def _policy(table: str) -> str:
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	return (
		f"CREATE POLICY {table}_tenant ON {table} "
		f"USING {predicate} WITH CHECK {predicate}"
	)


def upgrade() -> None:
	op.create_table(
		"morning_views",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("local_date", sa.Date(), nullable=False),
		sa.Column("payload", postgresql.JSONB(), nullable=False),
		sa.Column(
			"built_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.UniqueConstraint("user_id", "local_date", name="uq_morning_views_user_date"),
	)

	op.create_table(
		"resurfacings",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column("derivation_id", sa.Uuid(), nullable=False),
		sa.Column("shown_on", sa.Date(), nullable=False),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.ForeignKeyConstraint(
			["derivation_id", "user_id"],
			["derivations.id", "derivations.user_id"],
			name="fk_resurfacings_derivation_same_user",
			ondelete="CASCADE",
		),
	)
	op.create_index(
		"ix_resurfacings_user_derivation_shown",
		"resurfacings",
		["user_id", "derivation_id", "shown_on"],
	)

	op.execute(
		f"GRANT SELECT, INSERT, UPDATE, DELETE ON morning_views, resurfacings "
		f"TO {APP_ROLE}"
	)
	for table in ("morning_views", "resurfacings"):
		op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
		op.execute(_policy(table))


def downgrade() -> None:
	op.drop_table("resurfacings")
	op.drop_table("morning_views")
