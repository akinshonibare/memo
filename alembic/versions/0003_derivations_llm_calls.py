"""derivations, llm_calls, captures.extraction_raw

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-28

Hand-written. derivations is the polymorphic table ADR 0008 commits to: a new kind is
a Pydantic payload and a query, never a table. user_id is denormalised and pinned to
the capture's by a composite FK so it cannot drift (ADR 0010).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
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
		"derivations",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column("capture_id", sa.Uuid(), nullable=False),
		sa.Column("kind", sa.String(16), nullable=False),
		sa.Column("payload", postgresql.JSONB(), nullable=False),
		sa.Column("status", sa.String(16), nullable=False),
		sa.Column("extractor_version", sa.String(64), nullable=False),
		sa.Column("confidence", sa.Float(), nullable=True),
		sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
		sa.UniqueConstraint("id", "user_id", name="uq_derivations_id_user"),
		sa.CheckConstraint(
			"kind IN ('todo', 'event', 'idea')", name="ck_derivations_kind"
		),
		sa.CheckConstraint(
			"status IN ('pending_review', 'confirmed', 'rejected', 'user_created')",
			name="ck_derivations_status",
		),
		sa.ForeignKeyConstraint(
			["capture_id", "user_id"],
			["captures.id", "captures.user_id"],
			name="fk_derivations_capture_same_user",
			ondelete="CASCADE",
		),
	)
	op.create_index(
		"ix_derivations_user_kind_status", "derivations", ["user_id", "kind", "status"]
	)
	op.create_index(
		"ix_derivations_user_created",
		"derivations",
		["user_id", sa.text("created_at DESC")],
	)
	op.create_index("ix_derivations_capture", "derivations", ["capture_id"])
	op.create_index(
		"ix_derivations_payload",
		"derivations",
		["payload"],
		postgresql_using="gin",
	)

	op.create_table(
		"llm_calls",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("capture_id", sa.Uuid(), nullable=True),
		sa.Column("stage", sa.String(16), nullable=False),
		sa.Column("provider", sa.String(32), nullable=False),
		sa.Column("model", sa.String(64), nullable=False),
		sa.Column("input_tokens", sa.Integer(), nullable=False),
		sa.Column("output_tokens", sa.Integer(), nullable=False),
		sa.Column(
			"cache_read_tokens", sa.Integer(), nullable=False, server_default="0"
		),
		sa.Column(
			"cache_write_tokens", sa.Integer(), nullable=False, server_default="0"
		),
		sa.Column("latency_ms", sa.Integer(), nullable=False),
		sa.Column("cost_usd", sa.Numeric(12, 6), nullable=False),
		sa.Column("request_id", sa.String(64), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
	)
	op.create_index("ix_llm_calls_user_created", "llm_calls", ["user_id", "created_at"])

	# Raw model output kept only when validation failed terminally (ADR 0015).
	op.add_column("captures", sa.Column("extraction_raw", sa.Text(), nullable=True))

	op.execute(
		f"GRANT SELECT, INSERT, UPDATE, DELETE ON derivations, llm_calls TO {APP_ROLE}"
	)
	op.execute("ALTER TABLE derivations ENABLE ROW LEVEL SECURITY")
	op.execute("ALTER TABLE llm_calls ENABLE ROW LEVEL SECURITY")
	op.execute(_policy("derivations"))
	op.execute(_policy("llm_calls"))


def downgrade() -> None:
	op.drop_column("captures", "extraction_raw")
	op.drop_table("llm_calls")
	op.drop_table("derivations")
