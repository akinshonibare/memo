import datetime as dt
import uuid

from sqlalchemy import (
	CheckConstraint,
	DateTime,
	ForeignKey,
	Index,
	String,
	Text,
	UniqueConstraint,
	func,
	text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from memo.ids import uuid7
from memo.status import CaptureStatus

CAPTURE_KINDS = ("text", "audio")


def _in_list(values: tuple[str, ...]) -> str:
	return ", ".join(f"'{v}'" for v in values)


class Base(DeclarativeBase):
	type_annotation_map = {dt.datetime: DateTime(timezone=True)}


class User(Base):
	__tablename__ = "users"

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	email: Mapped[str | None] = mapped_column(String(320), unique=True)
	timezone: Mapped[str] = mapped_column(
		String(64), default="UTC", server_default="UTC"
	)
	created_at: Mapped[dt.datetime] = mapped_column(server_default=func.now())


class Capture(Base):
	__tablename__ = "captures"
	__mapper_args__ = {"eager_defaults": True}

	id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
	user_id: Mapped[uuid.UUID] = mapped_column(
		ForeignKey("users.id", ondelete="CASCADE")
	)
	kind: Mapped[str] = mapped_column(String(16))
	text: Mapped[str | None] = mapped_column(Text)
	status: Mapped[str] = mapped_column(
		String(16), default=CaptureStatus.PENDING.value, server_default="pending"
	)
	failure_reason: Mapped[str | None] = mapped_column(Text)
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
