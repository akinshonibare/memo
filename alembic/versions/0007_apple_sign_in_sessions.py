"""users.apple_sub and a sessions table: Sign in with Apple, server-issued sessions

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28

Identity now comes from Apple's identity token (ADR 0019). The server never stores
that token; it issues its own opaque session token and stores only a SHA-256 hash of
it. Sessions carry the tenant policy like every other user-owned table; the login
and token-resolution paths run under the system bypass because they happen before
the request has a user.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"


def upgrade() -> None:
	op.add_column("users", sa.Column("apple_sub", sa.String(255), nullable=True))
	op.create_unique_constraint("uq_users_apple_sub", "users", ["apple_sub"])

	op.create_table(
		"sessions",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
		sa.Column("device_name", sa.String(120), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.func.now(),
			nullable=False,
		),
		sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
		sa.Column(
			"last_seen_at",
			sa.DateTime(timezone=True),
			server_default=sa.func.now(),
			nullable=False,
		),
		sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
	)
	op.create_index("ix_sessions_user_id", "sessions", ["user_id"])

	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON sessions TO {APP_ROLE}")
	op.execute("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY")
	op.execute("ALTER TABLE sessions FORCE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY sessions_tenant ON sessions "
		f"USING {predicate} WITH CHECK {predicate}"
	)


def downgrade() -> None:
	op.execute("DROP POLICY sessions_tenant ON sessions")
	op.drop_index("ix_sessions_user_id", table_name="sessions")
	op.drop_table("sessions")
	op.drop_constraint("uq_users_apple_sub", "users", type_="unique")
	op.drop_column("users", "apple_sub")
