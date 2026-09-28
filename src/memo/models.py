import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
	BigInteger,
	CheckConstraint,
	Computed,
	Date,
	DateTime,
	Float,
	ForeignKey,
	ForeignKeyConstraint,
	Index,
	Numeric,
	String,
	Text,
	UniqueConstraint,
	func,
	text,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from memo.ids import uuid7
from memo.schemas.derivations import DERIVATION_STATUSES, KINDS
from memo.status import CaptureStatus

CAPTURE_KINDS = ("text", "audio")
JOB_RUN_STATUSES = ("running", "succeeded", "failed")


def _in_list(values: tuple[str, ...]) -> str:
	return ", ".join(f"'{v}'" for v in values)


class Base(DeclarativeBase):
	type_annotation_map = {dt.datetime: DateTime(timezone=True)}


class User(Base):
	__tablename__ = "users"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	email: Mapped[str | None] = mapped_column(String(320), unique=True)
	# Apple's stable per-app user identifier (ADR 0019). Null until first sign-in.
	apple_sub: Mapped[str | None] = mapped_column(String(255), unique=True)
	timezone: Mapped[str] = mapped_column(
		String(64), default="UTC", server_default="UTC"
	)
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Session(Base):
	"""A server-issued bearer session. Only the hash of the token is stored; the
	token itself exists in the device's Keychain and nowhere else."""

	__tablename__ = "sessions"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE"), index=True
	)
	token_hash: Mapped[str] = mapped_column(String(64), unique=True)
	device_name: Mapped[str | None] = mapped_column(String(120))
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	expires_at: Mapped[dt.datetime]
	last_seen_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	revoked_at: Mapped[dt.datetime | None]


class Recording(Base):
	"""An audio asset in object storage. 1:1 with a capture once attached."""

	__tablename__ = "recordings"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	storage_key: Mapped[str] = mapped_column(Text, unique=True)
	mime: Mapped[str] = mapped_column(String(64))
	bytes: Mapped[int | None] = mapped_column(BigInteger)
	duration_ms: Mapped[int | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

	__table_args__ = (UniqueConstraint("id", "user_id", name="uq_recordings_id_user"),)


class Capture(Base):
	__tablename__ = "captures"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	kind: Mapped[str] = mapped_column(String(16))
	text: Mapped[str | None] = mapped_column(Text)
	recording_id: Mapped[uuid.UUID | None]
	status: Mapped[str] = mapped_column(
		String(16), default=CaptureStatus.PENDING.value, server_default="pending"
	)
	failure_reason: Mapped[str | None] = mapped_column(Text)
	# Raw model output, kept only when extraction failed validation terminally.
	extraction_raw: Mapped[str | None] = mapped_column(Text)
	attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
	client_token: Mapped[str] = mapped_column(String(128))
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	updated_at: Mapped[dt.datetime] = mapped_column(
		server_default=func.now(), onupdate=func.now()
	)

	__table_args__ = (
		UniqueConstraint(
			"user_id", "client_token", name="uq_captures_user_client_token"
		),
		# Target for the composite foreign keys on derivations and chunks (ADR 0010).
		UniqueConstraint("id", "user_id", name="uq_captures_id_user"),
		ForeignKeyConstraint(
			["recording_id", "user_id"],
			["recordings.id", "recordings.user_id"],
			name="fk_captures_recording_same_user",
			ondelete="SET NULL",
		),
		CheckConstraint(
			f"kind IN ({_in_list(CAPTURE_KINDS)})", name="ck_captures_kind"
		),
		CheckConstraint(
			f"status IN ({_in_list(tuple(s.value for s in CaptureStatus))})",
			name="ck_captures_status",
		),
	)


Index("ix_captures_user_created", Capture.user_id, Capture.created_at.desc())
Index(
	"ix_captures_failed",
	Capture.user_id,
	postgresql_where=text("status = 'failed'"),
)
Index(
	"uq_captures_recording",
	Capture.recording_id,
	unique=True,
	postgresql_where=text("recording_id IS NOT NULL"),
)
Index("ix_recordings_user_created", Recording.user_id, Recording.created_at)


class JobRun(Base):
	"""The idempotency ledger: one row per (capture, stage, version) ever attempted."""

	__tablename__ = "job_runs"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	capture_id: Mapped[uuid.UUID]
	user_id: Mapped[uuid.UUID]
	stage: Mapped[str] = mapped_column(String(32))
	version: Mapped[str] = mapped_column(String(64))
	status: Mapped[str] = mapped_column(String(16))
	attempt: Mapped[int] = mapped_column(default=1, server_default="1")
	started_at: Mapped[dt.datetime]
	finished_at: Mapped[dt.datetime | None]
	error: Mapped[str | None] = mapped_column(Text)

	__table_args__ = (
		UniqueConstraint(
			"capture_id", "stage", "version", name="uq_job_runs_capture_stage_version"
		),
		CheckConstraint(
			f"status IN ({_in_list(JOB_RUN_STATUSES)})", name="ck_job_runs_status"
		),
		ForeignKeyConstraint(
			["capture_id", "user_id"],
			["captures.id", "captures.user_id"],
			name="fk_job_runs_capture_same_user",
			ondelete="CASCADE",
		),
	)


class Derivation(Base):
	"""A typed extraction pointing back at its capture (ADR 0008). Polymorphic on
	`kind`; the payload's shape is enforced by the Pydantic model for that kind."""

	__tablename__ = "derivations"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID]
	# NULL once the note it came from is deleted: the note goes, what the user got
	# out of it stays (ADR 0022).
	capture_id: Mapped[uuid.UUID | None]
	kind: Mapped[str] = mapped_column(String(16))
	payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
	status: Mapped[str] = mapped_column(String(16))
	extractor_version: Mapped[str] = mapped_column(String(64))
	confidence: Mapped[float | None] = mapped_column(Float)
	reviewed_at: Mapped[dt.datetime | None]
	# Todos only. Separate from status: status is the review (is this real?), this is
	# what happened next. A confirmed todo that is done is still confirmed.
	completed_at: Mapped[dt.datetime | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	updated_at: Mapped[dt.datetime] = mapped_column(
		server_default=func.now(), onupdate=func.now()
	)

	__table_args__ = (
		UniqueConstraint("id", "user_id", name="uq_derivations_id_user"),
		CheckConstraint(f"kind IN ({_in_list(KINDS)})", name="ck_derivations_kind"),
		CheckConstraint(
			f"status IN ({_in_list(DERIVATION_STATUSES)})",
			name="ck_derivations_status",
		),
		ForeignKeyConstraint(
			["capture_id", "user_id"],
			["captures.id", "captures.user_id"],
			name="fk_derivations_capture_same_user",
			# Really ON DELETE SET NULL (capture_id), set in migration 0008: the
			# column-list form keeps user_id, and SQLAlchemy can't express it.
		),
	)


Index(
	"ix_derivations_user_kind_status",
	Derivation.user_id,
	Derivation.kind,
	Derivation.status,
)
Index("ix_derivations_user_created", Derivation.user_id, Derivation.created_at.desc())
Index("ix_derivations_capture", Derivation.capture_id)
Index("ix_derivations_payload", Derivation.payload, postgresql_using="gin")


class LLMCallRow(Base):
	"""One row per model call: what it cost, attributed to a user and a stage."""

	__tablename__ = "llm_calls"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	capture_id: Mapped[uuid.UUID | None]
	stage: Mapped[str] = mapped_column(String(16))
	provider: Mapped[str] = mapped_column(String(32))
	model: Mapped[str] = mapped_column(String(64))
	input_tokens: Mapped[int]
	output_tokens: Mapped[int]
	cache_read_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
	cache_write_tokens: Mapped[int] = mapped_column(default=0, server_default="0")
	latency_ms: Mapped[int]
	cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6))
	request_id: Mapped[str | None] = mapped_column(String(64))
	message_id: Mapped[uuid.UUID | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


Index("ix_llm_calls_user_created", LLMCallRow.user_id, LLMCallRow.created_at)


class Conversation(Base):
	__tablename__ = "conversations"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	title: Mapped[str | None] = mapped_column(Text)
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	updated_at: Mapped[dt.datetime] = mapped_column(
		server_default=func.now(), onupdate=func.now()
	)

	__table_args__ = (
		UniqueConstraint("id", "user_id", name="uq_conversations_id_user"),
	)


MESSAGE_STATUSES = ("pending", "streaming", "done", "failed")


class Message(Base):
	"""One turn. An assistant message exists as a row before generation starts, so a
	client that drops mid-answer reconnects to the same stream_url."""

	__tablename__ = "messages"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	conversation_id: Mapped[uuid.UUID]
	user_id: Mapped[uuid.UUID]
	role: Mapped[str] = mapped_column(String(16))
	text: Mapped[str | None] = mapped_column(Text)
	status: Mapped[str] = mapped_column(String(16))
	rewritten_query: Mapped[str | None] = mapped_column(Text)
	rewrite_ms: Mapped[int | None]
	ttft_ms: Mapped[int | None]
	error: Mapped[str | None] = mapped_column(Text)
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	completed_at: Mapped[dt.datetime | None]

	__table_args__ = (
		UniqueConstraint("id", "user_id", name="uq_messages_id_user"),
		CheckConstraint("role IN ('user', 'assistant')", name="ck_messages_role"),
		CheckConstraint(
			f"status IN ({_in_list(MESSAGE_STATUSES)})", name="ck_messages_status"
		),
		ForeignKeyConstraint(
			["conversation_id", "user_id"],
			["conversations.id", "conversations.user_id"],
			name="fk_messages_conversation_same_user",
			ondelete="CASCADE",
		),
	)


Index("ix_messages_conversation_created", Message.conversation_id, Message.created_at)


class MorningView(Base):
	"""Built ahead of time, once per local date. The payload is the API response."""

	__tablename__ = "morning_views"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	local_date: Mapped[dt.date] = mapped_column(Date)
	payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
	built_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

	__table_args__ = (
		UniqueConstraint("user_id", "local_date", name="uq_morning_views_user_date"),
	)


class Resurfacing(Base):
	"""What was shown when, so one idea is not resurfaced again for a while."""

	__tablename__ = "resurfacings"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID]
	derivation_id: Mapped[uuid.UUID]
	shown_on: Mapped[dt.date] = mapped_column(Date)
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

	__table_args__ = (
		ForeignKeyConstraint(
			["derivation_id", "user_id"],
			["derivations.id", "derivations.user_id"],
			name="fk_resurfacings_derivation_same_user",
			ondelete="CASCADE",
		),
	)


Index(
	"ix_resurfacings_user_derivation_shown",
	Resurfacing.user_id,
	Resurfacing.derivation_id,
	Resurfacing.shown_on,
)


class MessageCitation(Base):
	"""A retrieval artifact: which chunk grounded which answer, chosen by the system
	before generation. References a chunk id, never free text."""

	__tablename__ = "message_citations"

	message_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
	chunk_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
	user_id: Mapped[uuid.UUID]
	rank: Mapped[int]

	__table_args__ = (
		ForeignKeyConstraint(
			["message_id", "user_id"],
			["messages.id", "messages.user_id"],
			name="fk_message_citations_message_same_user",
			ondelete="CASCADE",
		),
		ForeignKeyConstraint(
			["chunk_id", "user_id"],
			["chunks.id", "chunks.user_id"],
			name="fk_message_citations_chunk_same_user",
			ondelete="CASCADE",
		),
	)


EMBEDDING_DIM = 1024


class Chunk(Base):
	"""The retrieval unit and what a citation addresses. Never deleted on re-chunk:
	superseded, so an old citation still resolves to the text it was grounded in."""

	__tablename__ = "chunks"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID]
	# NULL only on a derivation chunk whose note was deleted (ADR 0022); a text
	# chunk always has its capture (ck_chunks_has_owner).
	capture_id: Mapped[uuid.UUID | None]
	derivation_id: Mapped[uuid.UUID | None]
	ordinal: Mapped[int]
	text: Mapped[str] = mapped_column(Text)
	char_start: Mapped[int]
	char_end: Mapped[int]
	embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
	embedding_model: Mapped[str] = mapped_column(String(64))
	embedding_dim: Mapped[int]
	tsv: Mapped[str | None] = mapped_column(
		TSVECTOR, Computed("to_tsvector('english', text)", persisted=True)
	)
	superseded_at: Mapped[dt.datetime | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

	__table_args__ = (
		UniqueConstraint("id", "user_id", name="uq_chunks_id_user"),
		ForeignKeyConstraint(
			["capture_id", "user_id"],
			["captures.id", "captures.user_id"],
			name="fk_chunks_capture_same_user",
			# Really ON DELETE SET NULL (capture_id): see Derivation.
		),
		CheckConstraint(
			"capture_id IS NOT NULL OR derivation_id IS NOT NULL",
			name="ck_chunks_has_owner",
		),
		ForeignKeyConstraint(
			["derivation_id", "user_id"],
			["derivations.id", "derivations.user_id"],
			name="fk_chunks_derivation_same_user",
			ondelete="CASCADE",
		),
	)


Index("ix_chunks_user_created", Chunk.user_id, Chunk.created_at)
Index("ix_chunks_capture", Chunk.capture_id)
Index(
	"ix_chunks_user_live",
	Chunk.user_id,
	postgresql_where=text("superseded_at IS NULL"),
)
