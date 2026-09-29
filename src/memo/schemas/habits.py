"""Habits (ADR 0043): activities found in notes, habits you track, and what a habit's
days look like."""

import datetime as dt
import re
import uuid
from typing import Any, Literal

from pydantic import (
	BaseModel,
	ConfigDict,
	Field,
	ValidationInfo,
	field_validator,
	model_validator,
)

from memo.schemas.derivations import no_leaked_field

_KEY = re.compile(r"[^a-z ]+")


def normalize_key(value: str) -> str:
	"""A key as memo files it: lowercase letters and single spaces, at most 40."""
	return " ".join(_KEY.sub(" ", value.casefold()).split())[:40]


class ActivityFound(BaseModel):
	"""One thing the author did, found in a note (prompts/habits_v1). Every field is
	required in the output schema, for the reason on derivations._Item."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	key: str = Field(min_length=1, max_length=40)
	label: str = Field(min_length=1, max_length=120)
	occurred_on: dt.date
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	@field_validator("key")
	@classmethod
	def _clean_key(cls, value: str) -> str:
		key = normalize_key(value)
		if not key:
			raise ValueError("key must have letters in it")
		return key

	@field_validator("label", mode="after")
	@classmethod
	def _no_leaked_fields(cls, value: str) -> str:
		return no_leaked_field(value.strip())  # type: ignore[no-any-return]

	@field_validator("occurred_on")
	@classmethod
	def _not_after_the_note(cls, value: dt.date, info: ValidationInfo) -> dt.date:
		# Validation context carries the note's own date: an activity "done" after the
		# note was written is a plan read as done, and goes back for a repair.
		captured_on = (info.context or {}).get("captured_on")
		if captured_on is not None and value > captured_on:
			raise ValueError("occurred_on is after the note was written")
		return value


class HabitsExtraction(BaseModel):
	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	activities: list[ActivityFound] = Field(default_factory=list)


Source = Literal["activity", "family", "bible"]


class HabitOut(BaseModel):
	id: uuid.UUID
	key: str
	name: str
	source: str
	target_per_week: int | None
	this_week: int  # days, Monday to Sunday in your timezone
	week_days: list[bool]  # this week, Monday first: done that day?
	last_done: dt.date | None
	total_days: int
	since: dt.date | None  # the first day memo knows of
	weeks: list[int]  # the last 8 weeks' days, oldest first, this week last


class Suggestion(BaseModel):
	key: str
	name: str
	source: str
	days: int  # different days in the last 21
	total_days: int  # every day memo knows of


class HabitsOut(BaseModel):
	enabled: bool
	habits: list[HabitOut]
	suggestion: Suggestion | None


class Evidence(BaseModel):
	"""Why a day counts: a note, a tap, a contact, a reading."""

	kind: Literal["note", "logged", "contact", "reading", "passage"]
	text: str | None = None
	capture_id: uuid.UUID | None = None
	activity_id: uuid.UUID | None = None


class DayOut(BaseModel):
	date: dt.date
	evidence: list[Evidence]
	# Whether "Remove" can take this day away here: noticed or logged activities can;
	# a contact or a reading belongs to People or Faith.
	removable: bool


class HabitDetail(HabitOut):
	days: list[DayOut]  # the last 60 days that count, newest first


class HabitCreate(BaseModel):
	model_config = ConfigDict(extra="forbid")

	name: str = Field(min_length=1, max_length=80)
	key: str | None = Field(default=None, max_length=40)  # from a suggestion
	target_per_week: int | None = Field(default=None, ge=1, le=7)

	@field_validator("name")
	@classmethod
	def _name(cls, value: str) -> str:
		value = " ".join(value.split())
		if not value:
			raise ValueError("name must not be blank")
		return value


class HabitPatch(BaseModel):
	model_config = ConfigDict(extra="forbid")

	name: str | None = Field(default=None, min_length=1, max_length=80)
	target_per_week: int | None = Field(default=None, ge=1, le=7)

	@model_validator(mode="after")
	def _something(self) -> "HabitPatch":
		if not self.model_fields_set:
			raise ValueError("provide name or target_per_week")
		return self


class Dismiss(BaseModel):
	model_config = ConfigDict(extra="forbid")

	key: str = Field(min_length=1, max_length=40)


class LogDay(BaseModel):
	model_config = ConfigDict(extra="forbid")

	date: dt.date | None = None  # today, in your timezone


class HabitPrompt(BaseModel):
	"""Today's one habit line (ADR 0043): a suggestion, or a gentle nudge."""

	kind: Literal["suggest", "nudge"]
	key: str
	name: str
	source: str
	habit_id: uuid.UUID | None = None
	days: int = 0  # suggest: different days in the last 21
	last_done: dt.date | None = None  # nudge


class HabitWeek(BaseModel):
	habit_id: uuid.UUID
	name: str
	days: int
	target_per_week: int | None


def activity_payload(found: ActivityFound) -> dict[str, Any]:
	return {
		"key": found.key,
		"label": found.label,
		"occurred_on": found.occurred_on.isoformat(),
	}
