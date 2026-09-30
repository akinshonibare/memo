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
	Time,
	UniqueConstraint,
	false,
	func,
	text,
	true,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from memo.ids import uuid7
from memo.schemas.derivations import ALL_KINDS, DERIVATION_STATUSES, PERSON_KINDS
from memo.status import CaptureStatus

CAPTURE_KINDS = ("text", "audio", "image")
# Where a note lives (ADR 0042): with the rest, or in Faith. Faith notes are hidden
# from Home, search, Ask and On this day only while the user has Faith turned on;
# turning it off brings them back, so nothing is ever lost behind a switch.
SPACES = ("main", "faith")
# The translations a Bible link can open in (ADR 0042); the app maps each to a
# bible.com version.
BIBLE_TRANSLATIONS = ("NIV", "ESV", "KJV", "NKJV", "NLT", "CSB", "NASB", "MSG")
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
	# What the assistant calls the person; None means "you". Seeded from Apple's
	# full name on first sign-in, the only time Apple sends it.
	display_name: Mapped[str | None] = mapped_column(String(60))
	# An Aura-2 model for spoken answers; None means the server default.
	voice: Mapped[str | None] = mapped_column(String(64))
	# Keep what extraction finds without asking (ADR 0030); stored as 'accepted'.
	auto_accept: Mapped[bool] = mapped_column(default=False, server_default=false())
	# Faith (ADR 0042): a space for prayers, what you're reading and faith notes. Off
	# by default; on the account, not the phone, because the server hides faith notes
	# from Home only while it is on.
	faith_enabled: Mapped[bool] = mapped_column(default=False, server_default=false())
	bible_translation: Mapped[str] = mapped_column(
		String(8), default="NIV", server_default="NIV"
	)
	# What you're reading now, as you set it: a passage payload ("John" is the whole
	# book), or None. There is no fixed plan (ADR 0042, revised).
	bible_reading: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
	# Habits (ADRs 0043, 0054): always on; every note is read for what you did. No
	# setting reaches it any more: false is the operator's switch, if the cost of
	# reading every note ever needs one.
	habits_enabled: Mapped[bool] = mapped_column(default=True, server_default=true())
	# The morning push (ADR 0047): the local time it arrives, or NULL for none.
	morning_push_at: Mapped[dt.time | None] = mapped_column(Time())
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

	__table_args__ = (
		CheckConstraint(
			f"bible_translation IN ({_in_list(BIBLE_TRANSLATIONS)})",
			name="ck_users_bible_translation",
		),
	)


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
	"""An uploaded media asset in object storage - audio, or since ADR 0024 a photo.
	The name predates photos; it is kept because the API contract installed apps use
	says `recording_id`. 1:1 with a capture once attached."""

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
	# A voice note's words with their times, [[start_ms, end_ms, word], ...], written
	# alongside `text` by the transcribe stage (ADR 0039). NULL: not a voice note, or
	# transcribed before words were kept.
	words: Mapped[list[Any] | None] = mapped_column(JSONB)
	# A photo's own words, written with it (ADR 0056). `text` is rebuilt from them and
	# the description, so this is the source that survives reprocessing.
	caption: Mapped[str | None] = mapped_column(Text)
	recording_id: Mapped[uuid.UUID | None]
	status: Mapped[str] = mapped_column(
		String(16), default=CaptureStatus.PENDING.value, server_default="pending"
	)
	failure_reason: Mapped[str | None] = mapped_column(Text)
	# Raw model output, kept only when extraction failed validation terminally.
	extraction_raw: Mapped[str | None] = mapped_column(Text)
	attempt_count: Mapped[int] = mapped_column(default=0, server_default="0")
	client_token: Mapped[str] = mapped_column(String(128))
	space: Mapped[str] = mapped_column(String(8), default="main", server_default="main")
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
			"caption IS NULL OR kind = 'image'", name="ck_captures_caption_on_photos"
		),
		CheckConstraint(
			f"status IN ({_in_list(tuple(s.value for s in CaptureStatus))})",
			name="ck_captures_status",
		),
		CheckConstraint(f"space IN ({_in_list(SPACES)})", name="ck_captures_space"),
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


class Person(Base):
	"""Someone the user's notes are about (ADR 0029). The one entity ADR 0008 allows
	outside the capture -> derivation shape: a person exists independently of any
	one note, and `mention` derivations point at them. Nothing here is typed in at
	capture time; the people stage creates a person the first time a name has no
	match, and the user corrects from there (rename, alias, merge)."""

	__tablename__ = "people"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	name: Mapped[str] = mapped_column(String(120))
	# Other names a note may use for them: "Mum", a nickname, a misheard spelling.
	aliases: Mapped[list[str]] = mapped_column(
		ARRAY(String(120)), default=list, server_default="{}"
	)
	# A few words that tell two people with one name apart: "from work", "cousin".
	note: Mapped[str | None] = mapped_column(String(120))
	# How often to suggest getting in touch. NULL: never. New people get 30.
	reach_out_every_days: Mapped[int | None]
	snoozed_until: Mapped[dt.date | None] = mapped_column(Date)
	# Family, for the "time with family" habit (ADR 0043). NULL: memo guesses from the
	# name or note; true or false: you said.
	family: Mapped[bool | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	updated_at: Mapped[dt.datetime] = mapped_column(
		server_default=func.now(), onupdate=func.now()
	)

	__table_args__ = (
		# Target for the composite foreign key on derivations (ADR 0010).
		UniqueConstraint("id", "user_id", name="uq_people_id_user"),
		CheckConstraint("length(btrim(name)) > 0", name="ck_people_name"),
		CheckConstraint(
			"reach_out_every_days IS NULL OR reach_out_every_days BETWEEN 1 AND 365",
			name="ck_people_reach_out_every_days",
		),
	)


Index("ix_people_user", Person.user_id)


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
	# Mentions only (ADR 0029): who this is. NULL while the user is being asked
	# "which one?", or once the person is deleted.
	person_id: Mapped[uuid.UUID | None]
	# Updates only (ADR 0048): the earlier item a note would change. A suggestion goes
	# with its item: deleting the prayer or idea deletes what pointed at it.
	target_id: Mapped[uuid.UUID | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	updated_at: Mapped[dt.datetime] = mapped_column(
		server_default=func.now(), onupdate=func.now()
	)

	__table_args__ = (
		UniqueConstraint("id", "user_id", name="uq_derivations_id_user"),
		CheckConstraint(f"kind IN ({_in_list(ALL_KINDS)})", name="ck_derivations_kind"),
		CheckConstraint(
			f"status IN ({_in_list(DERIVATION_STATUSES)})",
			name="ck_derivations_status",
		),
		ForeignKeyConstraint(
			["capture_id", "user_id"],
			["captures.id", "captures.user_id"],
			name="fk_derivations_capture_same_user",
			# Deleting a note deletes what was taken from it (ADR 0053, migration
			# 0022). capture_id stays nullable for items detached before that.
			ondelete="CASCADE",
		),
		ForeignKeyConstraint(
			["person_id", "user_id"],
			["people.id", "people.user_id"],
			name="fk_derivations_person_same_user",
			# Really ON DELETE SET NULL (person_id), set in migration 0012.
		),
		CheckConstraint(
			f"person_id IS NULL OR kind IN ({_in_list(PERSON_KINDS)})",
			name="ck_derivations_person_kinds",
		),
		ForeignKeyConstraint(
			["target_id", "user_id"],
			["derivations.id", "derivations.user_id"],
			name="fk_derivations_target_same_user",
			ondelete="CASCADE",
		),
		CheckConstraint(
			"(target_id IS NULL) = (kind <> 'update')",
			name="ck_derivations_target_update",
		),
	)


Index(
	"ix_derivations_user_person",
	Derivation.user_id,
	Derivation.person_id,
	postgresql_where=text("person_id IS NOT NULL"),
)
Index(
	"ix_derivations_user_kind_status",
	Derivation.user_id,
	Derivation.kind,
	Derivation.status,
)
Index("ix_derivations_user_created", Derivation.user_id, Derivation.created_at.desc())
Index("ix_derivations_capture", Derivation.capture_id)
Index(
	"ix_derivations_target",
	Derivation.user_id,
	Derivation.target_id,
	postgresql_where=text("target_id IS NOT NULL"),
)
Index("ix_derivations_payload", Derivation.payload, postgresql_using="gin")
# One activity per Apple Health workout (ADR 0050, migration 0021): two syncs at once
# can't file the same workout twice.
HEALTH_WORKOUT_WHERE = text(
	"kind = 'activity' AND (payload ->> 'health_id') IS NOT NULL"
)
Index(
	"uq_derivations_health_workout",
	Derivation.user_id,
	text("(payload ->> 'health_id')"),
	unique=True,
	postgresql_where=HEALTH_WORKOUT_WHERE,
)


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
	# The note's space, copied here like user_id (ADR 0042): retrieval filters on this
	# table alone, so a faith note stays out of Home's search and Ask without a join.
	# A derivation chunk is always 'main': items from a faith note are still items.
	space: Mapped[str] = mapped_column(String(8), default="main", server_default="main")

	__table_args__ = (
		UniqueConstraint("id", "user_id", name="uq_chunks_id_user"),
		CheckConstraint(f"space IN ({_in_list(SPACES)})", name="ck_chunks_space"),
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


HABIT_SOURCES = ("activity", "family", "bible")
HABIT_STATUSES = ("active", "dismissed")


class Habit(Base):
	"""A habit you track, or a suggestion you dismissed (ADR 0043). What counts is
	computed from notes and other records, never stored here: a stored count would
	have to follow every rejection, edit and merge, and would drift."""

	__tablename__ = "habits"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	# The activity key it counts ("gym", "reading"), or "family" / "bible".
	key: Mapped[str] = mapped_column(String(40))
	name: Mapped[str] = mapped_column(String(80))
	source: Mapped[str] = mapped_column(String(16))
	status: Mapped[str] = mapped_column(
		String(16), default="active", server_default="active"
	)
	target_per_week: Mapped[int | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())

	__table_args__ = (
		UniqueConstraint("user_id", "key", name="uq_habits_user_key"),
		CheckConstraint(
			f"source IN ({_in_list(HABIT_SOURCES)})", name="ck_habits_source"
		),
		CheckConstraint(
			f"status IN ({_in_list(HABIT_STATUSES)})", name="ck_habits_status"
		),
		CheckConstraint(
			"target_per_week IS NULL OR target_per_week BETWEEN 1 AND 7",
			name="ck_habits_target",
		),
		CheckConstraint("length(btrim(name)) > 0", name="ck_habits_name"),
	)


PUSH_KINDS = ("morning",)
PUSH_STATUSES = ("sending", "sent", "skipped", "failed")
PUSH_FEEDBACK = ("up", "down")


class PushToken(Base):
	"""A phone memo can push to (ADR 0047): an Expo push token, registered under the
	session that signed that phone in. Pushes go only to tokens whose session is still
	live, so signing out, or "sign out everywhere" for a lost phone, stops them without
	the phone having to say anything."""

	__tablename__ = "push_tokens"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	session_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("sessions.id", ondelete="CASCADE")
	)
	# One account at a time: a phone signed into another account moves its token.
	token: Mapped[str] = mapped_column(String(200), unique=True)
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	last_seen_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	# Apple said the app is gone from that phone (DeviceNotRegistered).
	disabled_at: Mapped[dt.datetime | None]


class Push(Base):
	"""One push memo meant to send, and what happened to it (ADR 0047). The ledger that
	makes it once a day (unique per user, kind and local date, claimed before sending),
	and the feedback the brief asks for from day one: opened, and thumbs up or down."""

	__tablename__ = "pushes"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	kind: Mapped[str] = mapped_column(String(16))
	local_date: Mapped[dt.date] = mapped_column(Date)
	status: Mapped[str] = mapped_column(String(16))
	# Why a push wasn't sent ("nothing today", "no phone"), or why it failed.
	reason: Mapped[str | None] = mapped_column(Text)
	title: Mapped[str | None] = mapped_column(Text)
	body: Mapped[str | None] = mapped_column(Text)
	# Expo's ticket per phone, [{"token_id": ..., "ticket": ...}], for the receipts.
	tickets: Mapped[list[Any]] = mapped_column(JSONB, default=list, server_default="[]")
	receipts_checked_at: Mapped[dt.datetime | None]
	opened_at: Mapped[dt.datetime | None]
	feedback: Mapped[str | None] = mapped_column(String(8))
	feedback_at: Mapped[dt.datetime | None]
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())
	sent_at: Mapped[dt.datetime | None]

	__table_args__ = (
		UniqueConstraint("user_id", "kind", "local_date", name="uq_pushes_user_day"),
		CheckConstraint(f"kind IN ({_in_list(PUSH_KINDS)})", name="ck_pushes_kind"),
		CheckConstraint(
			f"status IN ({_in_list(PUSH_STATUSES)})", name="ck_pushes_status"
		),
		CheckConstraint(
			f"feedback IS NULL OR feedback IN ({_in_list(PUSH_FEEDBACK)})",
			name="ck_pushes_feedback",
		),
	)


Index("ix_push_tokens_user", PushToken.user_id)
Index("ix_pushes_user_created", Push.user_id, Push.created_at.desc())
Index(
	"ix_pushes_receipts_due",
	Push.sent_at,
	postgresql_where=text("status = 'sent' AND receipts_checked_at IS NULL"),
)
