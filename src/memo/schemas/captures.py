import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memo.schemas.derivations import DerivationOut
from memo.schemas.people import MentionOut


class CaptureCreate(BaseModel):
	# extra="forbid" so a user_id smuggled into the body is a 422, not silently ignored.
	model_config = ConfigDict(extra="forbid")

	text: str | None = Field(default=None, min_length=1, max_length=50_000)
	recording_id: uuid.UUID | None = None
	client_token: str = Field(
		min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$"
	)

	# When the note was actually taken, for one queued offline and synced later
	# (ADR 0028). Its date is what "tomorrow" in the note resolves against, so the
	# sync time would be wrong. Bounded: not in the future, not older than 90 days.
	captured_at: dt.datetime | None = None

	@field_validator("captured_at")
	@classmethod
	def _plausible(cls, value: dt.datetime | None) -> dt.datetime | None:
		if value is None:
			return value
		if value.tzinfo is None:
			raise ValueError("captured_at needs a timezone offset")
		now = dt.datetime.now(dt.UTC)
		if value > now + dt.timedelta(minutes=5):
			raise ValueError("captured_at is in the future")
		if value < now - dt.timedelta(days=90):
			raise ValueError("captured_at is more than 90 days ago")
		return value

	@model_validator(mode="after")
	def exactly_one_source(self) -> "CaptureCreate":
		if (self.text is None) == (self.recording_id is None):
			raise ValueError("provide exactly one of text or recording_id")
		return self


class CaptureOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	kind: str
	text: str | None
	recording_id: uuid.UUID | None
	status: str
	failure_reason: str | None
	attempt_count: int
	created_at: dt.datetime
	# From the attached recording; filled in by the list and detail routes.
	duration_ms: int | None = None


class AudioOut(BaseModel):
	url: str
	expires_at: dt.datetime
	mime: str
	duration_ms: int | None


class CaptureDetail(CaptureOut):
	derivations: list[DerivationOut]  # the items: todos, events, ideas
	# Who the note is about (ADR 0029). A separate field so an app built before
	# people never renders a mention as an item.
	people: list[MentionOut] = []


class CapturePage(BaseModel):
	items: list[CaptureOut]
	next_cursor: str | None
