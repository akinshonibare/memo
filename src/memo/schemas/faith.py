"""Faith (ADR 0042): prayers, passages, what you're reading, and the one thing a day
the Faith screen remembers."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memo.schemas.derivations import no_leaked_field

Space = Literal["main", "faith"]


class PrayerFound(BaseModel):
	"""One prayer the faith reader found in a note (prompts/faith_v1). Every field is
	required in the output schema, for the reason given on derivations._Item."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	title: str = Field(min_length=1, max_length=200)
	for_person: str | None = Field(default=None, max_length=120)
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	@field_validator("title")
	@classmethod
	def _title_has_content(cls, value: str) -> str:
		stripped = value.strip()
		if not stripped:
			raise ValueError("title must not be blank")
		return stripped

	@field_validator("title", "for_person", mode="after")
	@classmethod
	def _no_leaked_fields(cls, value: str | None) -> str | None:
		return no_leaked_field(value)  # type: ignore[no-any-return]


class FaithExtraction(BaseModel):
	"""What the faith reader returns for one note. An empty list is the usual answer."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	prayers: list[PrayerFound] = Field(default_factory=list)


class PrayerOut(BaseModel):
	id: uuid.UUID
	title: str
	notes: str | None
	# How it was answered, in the user's words. None while still being prayed for.
	answer: str | None
	person_id: uuid.UUID | None
	person_name: str | None
	# The note it was said in, when it came from one.
	capture_id: uuid.UUID | None
	created_at: dt.datetime
	answered_at: dt.datetime | None


class PrayerCreate(BaseModel):
	model_config = ConfigDict(extra="forbid")

	title: str = Field(min_length=1, max_length=200)
	notes: str | None = Field(default=None, max_length=2000)
	person_id: uuid.UUID | None = None


class PrayerPatch(BaseModel):
	"""Any of: edit it, say who it's for (null clears), mark it answered (with how) or
	not, or remove it. Removing is a rejection, not a delete, like every item."""

	model_config = ConfigDict(extra="forbid")

	title: str | None = Field(default=None, min_length=1, max_length=200)
	notes: str | None = Field(default=None, max_length=2000)
	answer: str | None = Field(default=None, max_length=2000)
	person_id: uuid.UUID | None = None
	answered: bool | None = None
	removed: Literal[True] | None = None

	@model_validator(mode="after")
	def _something(self) -> "PrayerPatch":
		if not self.model_fields_set:
			raise ValueError("provide something to change")
		return self


class PassageOut(BaseModel):
	ref: str  # "Romans 8:28–30"
	book: str  # USFM: ROM
	chapter: int
	verse: int | None
	end_chapter: int
	end_verse: int | None


class ReadingLogOut(BaseModel):
	"""A passage you read, on a day."""

	id: uuid.UUID
	passage: PassageOut
	on: dt.date


class ReadingState(BaseModel):
	"""What you're reading, as you set it, and what memo offers to read next in it:
	the chapter after the last one you read there. No plan, and nothing behind."""

	current: PassageOut | None  # what you set; None until you set something
	next: PassageOut | None  # None when nothing is set, or you've read to its end
	last: ReadingLogOut | None  # the latest reading, which Undo takes back
	recent: list[ReadingLogOut]  # newest first, a few


class SetReading(BaseModel):
	model_config = ConfigDict(extra="forbid")

	ref: str | None = Field(max_length=120)  # "John", "Psalms", "Romans 8"; null clears


class LogReading(BaseModel):
	model_config = ConfigDict(extra="forbid")

	# What you read; omitted, the next chapter of what you're reading.
	ref: str | None = Field(default=None, max_length=120)
	on: dt.date | None = None  # today, in your timezone


class PassageMention(BaseModel):
	"""A note that names a passage: where it was said, and what was said around it."""

	capture_id: uuid.UUID
	on: dt.date  # the day the note was written, in the user's timezone
	excerpt: str | None


class PassageRef(BaseModel):
	"""One passage your notes talk about, and the notes that do (ADR 0042, revised):
	Faith as a reference to what you've written about, not a plan to keep up with."""

	passage: PassageOut
	count: int  # notes that name it
	last_on: dt.date
	notes: list[PassageMention]  # newest first, a few


class MomentOut(BaseModel):
	"""The Faith screen's one thing a day (ADR 0042), chosen like On this day: the same
	all day, and never a feed. A prayer from this date in another year, or else a
	passage the user wrote about, with what they wrote."""

	kind: Literal["prayer", "passage"]
	prayer: PrayerOut | None = None
	# For a prayer: the day it was prayed. For a passage: the day it was written about.
	on: dt.date
	passage: PassageOut | None = None
	capture_id: uuid.UUID | None = None
	excerpt: str | None = None


class FaithOut(BaseModel):
	enabled: bool
	translation: str
	moment: MomentOut | None
	reading: ReadingState
	# Every passage the notes name, most recently written about first.
	passages: list[PassageRef]
	open: list[PrayerOut]
	answered: list[PrayerOut]  # most recent first


class PassageWritten(BaseModel):
	passage: PassageOut
	capture_id: uuid.UUID | None


class FaithWeek(BaseModel):
	"""The weekly review's Faith section: facts, nothing to beat."""

	read: list[ReadingLogOut]
	answered: list[PrayerOut]
	passages: list[PassageWritten]


class CaptureMove(BaseModel):
	model_config = ConfigDict(extra="forbid")

	space: Space
