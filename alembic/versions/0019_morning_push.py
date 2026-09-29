"""push: the phones memo may push to, and a ledger of what it pushed

Revision ID: 0019
Revises: 0018
Create Date: 2026-09-29

ADR 0047. All additive, and nothing is sent until the user sets a morning time:

- users.morning_push_at: the local time of the morning push, or NULL (off).
- push_tokens: an Expo push token per phone, under the session that signed it in, so
  a signed-out or revoked session stops receiving. Unique by token: one account at a
  time. RLS like every user table.
- pushes: one row per push memo meant to send, unique per user, kind and local date
  (the at-most-once claim), with what happened and the feedback: opened, up or down.
  RLS.

Downgrade drops both tables and the column; nothing else refers to them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"


def _tenant_only(table: str) -> None:
	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO {APP_ROLE}")
	op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY {table}_tenant ON {table} "
		f"USING {predicate} WITH CHECK {predicate}"
	)


def upgrade() -> None:
	op.add_column("users", sa.Column("morning_push_at", sa.Time(), nullable=True))

	op.create_table(
		"push_tokens",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column(
			"session_id",
			sa.Uuid(),
			sa.ForeignKey("sessions.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("token", sa.String(200), nullable=False, unique=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.Column(
			"last_seen_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
	)
	op.create_index("ix_push_tokens_user", "push_tokens", ["user_id"])
	_tenant_only("push_tokens")

	op.create_table(
		"pushes",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("kind", sa.String(16), nullable=False),
		sa.Column("local_date", sa.Date(), nullable=False),
		sa.Column("status", sa.String(16), nullable=False),
		sa.Column("reason", sa.Text(), nullable=True),
		sa.Column("title", sa.Text(), nullable=True),
		sa.Column("body", sa.Text(), nullable=True),
		sa.Column("tickets", JSONB(), nullable=False, server_default="[]"),
		sa.Column("receipts_checked_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("feedback", sa.String(8), nullable=True),
		sa.Column("feedback_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
		sa.UniqueConstraint("user_id", "kind", "local_date", name="uq_pushes_user_day"),
		sa.CheckConstraint("kind IN ('morning')", name="ck_pushes_kind"),
		sa.CheckConstraint(
			"status IN ('sending', 'sent', 'skipped', 'failed')",
			name="ck_pushes_status",
		),
		sa.CheckConstraint(
			"feedback IS NULL OR feedback IN ('up', 'down')",
			name="ck_pushes_feedback",
		),
	)
	op.create_index(
		"ix_pushes_user_created", "pushes", ["user_id", sa.text("created_at DESC")]
	)
	op.create_index(
		"ix_pushes_receipts_due",
		"pushes",
		["sent_at"],
		postgresql_where=sa.text("status = 'sent' AND receipts_checked_at IS NULL"),
	)
	_tenant_only("pushes")


def downgrade() -> None:
	op.drop_table("pushes")
	op.drop_table("push_tokens")
	op.drop_column("users", "morning_push_at")
