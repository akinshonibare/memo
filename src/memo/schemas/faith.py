"""Faith (ADR 0042): prayers, passages, the reading plan, and the one thing a day the
Faith screen remembers."""

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Space = Literal["main", "faith"]


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


class ReadingOut(BaseModel):
	index: int
	label: str  # "Exodus 12–14"
	book: str  # USFM, for the link
	chapter: int  # the chapter the link opens at
	end_chapter: int


class PlanOut(BaseModel):
	plan: str
	title: str  # "Genesis to Revelation"
	cycle: int  # times through, starting at 1
	position: int  # readings marked read this time through
	total: int
	# The next reading; None when this time through is finished.
	next: ReadingOut | None
	# The one last marked read, which "Undo" takes back.
	last: ReadingOut | None
	last_read_at: dt.datetime | None


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
	plan: PlanOut
	moment: MomentOut | None
	open: list[PrayerOut]
	answered: list[PrayerOut]  # most recent first


class PassageWritten(BaseModel):
	passage: PassageOut
	capture_id: uuid.UUID | None


class FaithWeek(BaseModel):
	"""The weekly review's Faith section: facts, nothing to beat."""

	read: list[ReadingOut]
	answered: list[PrayerOut]
	passages: list[PassageWritten]


class CaptureMove(BaseModel):
	model_config = ConfigDict(extra="forbid")

	space: Space
