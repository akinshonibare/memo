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


Measure = Literal["distance", "duration", "weight"]

# Units the reader may use, and what each is in memo's own (km, minutes, kg). The model
# copies the unit the note used and code converts: arithmetic is not a model's job.
UNITS: dict[str, dict[str, float]] = {
	"distance": {"km": 1.0, "mi": 1.609344, "m": 0.001},
	"duration": {"min": 1.0, "h": 60.0},
	"weight": {"kg": 1.0, "lb": 0.45359237},
}
# Past these a number is a misreading ("ran 5000" is metres, or a typo), not a record.
LIMITS: dict[str, float] = {"distance": 300.0, "duration": 24 * 60.0, "weight": 500.0}


class AmountFound(BaseModel):
	"""A number the note gives for an activity (ADR 0050): how far, how long, how
	heavy. Light on purpose: enough for a milestone, never a training log."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	measure: Measure
	value: float = Field(gt=0)
	unit: Literal["km", "mi", "m", "min", "h", "kg", "lb"]
	lift: str = Field(default="", max_length=40)  # weight only: "squat", "bench press"

	@field_validator("lift")
	@classmethod
	def _clean_lift(cls, value: str) -> str:
		return normalize_key(value)

	@model_validator(mode="after")
	def _fits(self) -> "AmountFound":
		if self.unit not in UNITS[self.measure]:
			raise ValueError(f"{self.unit} is not a unit of {self.measure}")
		if self.base > LIMITS[self.measure]:
			raise ValueError(
				f"{self.value} {self.unit} is not a plausible {self.measure}"
			)
		if self.measure == "weight" and not self.lift:
			raise ValueError("a weight needs the lift it was for")
		if self.measure != "weight" and self.lift:
			raise ValueError("only a weight has a lift")
		return self

	@property
	def base(self) -> float:
		"""The value in km, minutes or kg."""
		return self.value * UNITS[self.measure][self.unit]


class ActivityFound(BaseModel):
	"""One thing the author did, found in a note (prompts/habits_v2). Every field is
	required in the output schema, for the reason on derivations._Item."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	key: str = Field(min_length=1, max_length=40)
	label: str = Field(min_length=1, max_length=120)
	occurred_on: dt.date
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)
	amounts: list[AmountFound] = Field(default_factory=list, max_length=6)

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
	"""Why a day counts: a note, a tap, a contact, a reading, a workout in Health."""

	kind: Literal["note", "logged", "contact", "reading", "passage", "health"]
	text: str | None = None
	capture_id: uuid.UUID | None = None
	activity_id: uuid.UUID | None = None


class DayOut(BaseModel):
	date: dt.date
	evidence: list[Evidence]
	# Whether "Remove" can take this day away here: noticed or logged activities can;
	# a contact or a reading belongs to People or Faith.
	removable: bool


class Milestone(BaseModel):
	"""Something a habit reached (ADR 0050), noticed rather than logged: a tenth day, a
	first 10k, the longest run or the heaviest squat so far. Computed on every read."""

	kind: Literal["days", "first", "longest", "heaviest"]
	title: str  # "First 10k", "Heaviest squat yet", "10th day"
	detail: str  # "10.2 km", "100 kg", "Gym"
	date: dt.date
	capture_id: uuid.UUID | None = None  # the note it came from, when there is one


class HabitDetail(HabitOut):
	days: list[DayOut]  # the last 60 days that count, newest first
	milestones: list[Milestone] = []  # newest first


class TodayMilestone(Milestone):
	"""A milestone from the last few days, for Today's one line about it."""

	habit_id: uuid.UUID
	name: str


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


def stored_amount(measure: str, value: float, lift: str = "") -> dict[str, Any]:
	"""An amount as an activity's payload keeps it: in km, minutes or kg."""
	out: dict[str, Any] = {"measure": measure, "value": round(value, 2)}
	if lift:
		out["lift"] = lift
	return out


def activity_payload(found: ActivityFound) -> dict[str, Any]:
	payload: dict[str, Any] = {
		"key": found.key,
		"label": found.label,
		"occurred_on": found.occurred_on.isoformat(),
	}
	if found.amounts:
		payload["amounts"] = [
			stored_amount(a.measure, a.base, a.lift) for a in found.amounts
		]
	return payload
