"""chunks: the retrieval unit, with an HNSW index and a generated tsvector

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-28

Hand-written. A chunk is a core entity, not an indexing byproduct: it is what
retrieval returns and what a citation addresses, and it carries the model that
produced its vector because re-embedding is a migration (ADR 0016). Rows are never
deleted on re-chunk - they are superseded - so citations keep pointing at text.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "memo_app"
TENANT = "NULLIF(current_setting('app.user_id', true), '')::uuid"
SYSTEM = "current_setting('app.bypass_rls', true) = 'on'"
EMBEDDING_DIM = 1024


def upgrade() -> None:
	op.execute("CREATE EXTENSION IF NOT EXISTS vector")
	op.execute(
		f"""
		CREATE TABLE chunks (
			id uuid PRIMARY KEY,
			user_id uuid NOT NULL,
			capture_id uuid NOT NULL,
			derivation_id uuid NULL,
			ordinal integer NOT NULL,
			text text NOT NULL,
			char_start integer NOT NULL,
			char_end integer NOT NULL,
			embedding vector({EMBEDDING_DIM}) NOT NULL,
			embedding_model varchar(64) NOT NULL,
			embedding_dim integer NOT NULL,
			tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED,
			superseded_at timestamptz NULL,
			created_at timestamptz NOT NULL DEFAULT now(),
			CONSTRAINT uq_chunks_id_user UNIQUE (id, user_id),
			CONSTRAINT fk_chunks_capture_same_user
				FOREIGN KEY (capture_id, user_id)
				REFERENCES captures (id, user_id) ON DELETE CASCADE,
			CONSTRAINT fk_chunks_derivation_same_user
				FOREIGN KEY (derivation_id, user_id)
				REFERENCES derivations (id, user_id) ON DELETE CASCADE
		)
		"""
	)
	# HNSW: no training pass, better recall/latency than IVFFlat at this scale; m and
	# ef_construction left at pgvector's defaults (16, 64) until an eval says otherwise.
	op.execute(
		"CREATE INDEX ix_chunks_embedding_hnsw ON chunks "
		"USING hnsw (embedding vector_cosine_ops)"
	)
	op.execute("CREATE INDEX ix_chunks_tsv ON chunks USING gin (tsv)")
	op.create_index("ix_chunks_user_created", "chunks", ["user_id", "created_at"])
	op.create_index("ix_chunks_capture", "chunks", ["capture_id"])
	op.create_index(
		"ix_chunks_user_live",
		"chunks",
		["user_id"],
		postgresql_where=sa.text("superseded_at IS NULL"),
	)

	op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON chunks TO {APP_ROLE}")
	op.execute("ALTER TABLE chunks ENABLE ROW LEVEL SECURITY")
	predicate = f"(user_id = {TENANT} OR {SYSTEM})"
	op.execute(
		f"CREATE POLICY chunks_tenant ON chunks "
		f"USING {predicate} WITH CHECK {predicate}"
	)


def downgrade() -> None:
	op.drop_table("chunks")
