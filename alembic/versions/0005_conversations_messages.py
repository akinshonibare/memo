"""conversations, messages, message_citations; llm_calls.message_id

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-28

Hand-written. Citations reference chunk ids, never free text (design doc, Step 3):
a citation is a retrieval artifact chosen by the system before generation, so it can
be joined back to the passage and cannot be hallucinated.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
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
		"conversations",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column(
			"user_id",
			sa.Uuid(),
			sa.ForeignKey("users.id", ondelete="CASCADE"),
			nullable=False,
		),
		sa.Column("title", sa.Text(), nullable=True),
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
		sa.UniqueConstraint("id", "user_id", name="uq_conversations_id_user"),
	)
	op.create_index(
		"ix_conversations_user_updated",
		"conversations",
		["user_id", sa.text("updated_at DESC")],
	)

	op.create_table(
		"messages",
		sa.Column("id", sa.Uuid(), primary_key=True),
		sa.Column("conversation_id", sa.Uuid(), nullable=False),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column("role", sa.String(16), nullable=False),
		sa.Column("text", sa.Text(), nullable=True),
		sa.Column("status", sa.String(16), nullable=False),
		sa.Column("rewritten_query", sa.Text(), nullable=True),
		sa.Column("rewrite_ms", sa.Integer(), nullable=True),
		sa.Column("ttft_ms", sa.Integer(), nullable=True),
		sa.Column("error", sa.Text(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
		sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
		sa.UniqueConstraint("id", "user_id", name="uq_messages_id_user"),
		sa.CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
		sa.CheckConstraint(
			"status IN ('pending', 'streaming', 'done', 'failed')",
			name="ck_messages_status",
		),
		sa.ForeignKeyConstraint(
			["conversation_id", "user_id"],
			["conversations.id", "conversations.user_id"],
			name="fk_messages_conversation_same_user",
			ondelete="CASCADE",
		),
	)
	op.create_index(
		"ix_messages_conversation_created",
		"messages",
		["conversation_id", "created_at"],
	)

	op.create_table(
		"message_citations",
		sa.Column("message_id", sa.Uuid(), nullable=False),
		sa.Column("chunk_id", sa.Uuid(), nullable=False),
		sa.Column("user_id", sa.Uuid(), nullable=False),
		sa.Column("rank", sa.Integer(), nullable=False),
		sa.PrimaryKeyConstraint("message_id", "chunk_id", name="pk_message_citations"),
		sa.ForeignKeyConstraint(
			["message_id", "user_id"],
			["messages.id", "messages.user_id"],
			name="fk_message_citations_message_same_user",
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["chunk_id", "user_id"],
			["chunks.id", "chunks.user_id"],
			name="fk_message_citations_chunk_same_user",
		),
	)

	op.add_column("llm_calls", sa.Column("message_id", sa.Uuid(), nullable=True))

	op.execute(
		f"GRANT SELECT, INSERT, UPDATE, DELETE ON conversations, messages, "
		f"message_citations TO {APP_ROLE}"
	)
	for table in ("conversations", "messages", "message_citations"):
		op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
		op.execute(_policy(table))


def downgrade() -> None:
	op.drop_column("llm_calls", "message_id")
	op.drop_table("message_citations")
	op.drop_table("messages")
	op.drop_table("conversations")
