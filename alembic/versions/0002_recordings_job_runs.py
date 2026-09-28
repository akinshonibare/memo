"""recordings, job_runs, captures.recording_id, and a system bypass for sweeps

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Hand-written. The policies from 0001 are recreated with a second arm so cron sweeps
(which must see every tenant's rows) can opt in explicitly via set_config(
'app.bypass_rls', 'on', true) - transaction-scoped, so it dies with the sweep.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"


def _policy(table: str, column: str) -> str:
	predicate = f"({column} = {TENANT} OR {SYSTEM})"
	return (
		f"CREATE POLICY {table}_tenant ON {table} "
		f"USING {predicate} WITH CHECK {predicate}"
	)


def upgrade() -> None:
	op.create_table(
		"recordings",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("storage_key", sa.Text(), nullable=False, unique=True),
		sa.Column("mime", sa.String(64), nullable=False),
		sa.Column("bytes", sa.BigInteger(), nullable=True),
		sa.Column("duration_ms", sa.Integer(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.UniqueConstraint("id", "user_id", name="uq_recordings_id_user"),
	)
	op.create_index(
		"ix_recordings_user_created", "recordings", ["user_id", "created_at"]
	)

	op.add_column("captures", sa.Column("recording_id", sa.Uuid(), nullable=True))
	op.create_foreign_key(
		"fk_captures_recording_same_user",
		"captures",
		"recordings",
		["recording_id", "user_id"],
		["id", "user_id"],
		ondelete="SET NULL",
	)
	op.create_index(
		"uq_captures_recording",
		"captures",
		["recording_id"],
		unique=True,
		postgresql_where=sa.text("recording_id IS NOT NULL"),
	)

	op.create_table(
		"job_runs",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column("capture_id", sa.Uuid(), nullable=False),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column("stage", sa.String(32), nullable=False),
		sa.Column("version", sa.String(64), nullable=False),
		sa.Column("status", sa.String(16), nullable=False),
		sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"),
		sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
		sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("error", sa.Text(), nullable=True),
		sa.UniqueConstraint(
			"capture_id", "stage", "version", name="uq_job_runs_capture_stage_version"
		),
		sa.CheckConstraint(
			"status IN ('running', 'succeeded', 'failed')", name="ck_job_runs_status"
		),
		sa.ForeignKeyConstraint(
			["capture_id", "user_id"],
			["captures.id", "captures.user_id"],
			name="fk_job_runs_capture_same_user",
			ondelete="CASCADE",
		),
	)

	op.execute(
		f"GRANT SELECT, INSERT, UPDATE, DELETE ON recordings, job_runs TO {APP_ROLE}"
	)
	op.execute("ALTER TABLE recordings ENABLE ROW LEVEL SECURITY")
	op.execute("ALTER TABLE job_runs ENABLE ROW LEVEL SECURITY")

	op.execute("DROP POLICY users_self ON users")
	op.execute("DROP POLICY captures_tenant ON captures")
	op.execute(_policy("users", "id").replace("users_tenant", "users_self"))
	op.execute(_policy("captures", "user_id"))
	op.execute(_policy("recordings", "user_id"))
	op.execute(_policy("job_runs", "user_id"))


def downgrade() -> None:
	op.execute("DROP POLICY users_self ON users")
	op.execute("DROP POLICY captures_tenant ON captures")
	op.execute(
		f"CREATE POLICY users_self ON users "
		f"USING (id = {TENANT}) WITH CHECK (id = {TENANT})"
	)
	op.execute(
		f"CREATE POLICY captures_tenant ON captures "
		f"USING (user_id = {TENANT}) WITH CHECK (user_id = {TENANT})"
	)
	op.drop_table("job_runs")
	op.drop_index("uq_captures_recording", table_name="captures")
	op.drop_constraint(
		"fk_captures_recording_same_user", "captures", type_="foreignkey"
	)
	op.drop_column("captures", "recording_id")
	op.drop_table("recordings")
