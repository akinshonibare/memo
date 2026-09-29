"""Apple Health (ADR 0050): the workouts the phone read, sent a window at a time."""

import datetime as dt

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_WORKOUTS = 3000  # two years of a workout most days, in one request


def _aware(value: dt.datetime) -> dt.datetime:
	if value.tzinfo is None:
		raise ValueError("a time needs its timezone")
	return value


class WorkoutIn(BaseModel):
	"""One HealthKit workout, as the phone reads it: its type's raw number
	(HKWorkoutActivityType), when, how long, and how far if it has a distance."""

	model_config = ConfigDict(extra="forbid")

	id: str = Field(min_length=1, max_length=64)  # the workout's HealthKit UUID
	activity_type: int = Field(ge=0, le=100_000)
	start: dt.datetime
	end: dt.datetime
	duration_s: float = Field(ge=0, le=7 * 86_400)
	distance_m: float | None = Field(default=None, ge=0)

	_start_aware = field_validator("start", "end")(_aware)

	@model_validator(mode="after")
	def _in_order(self) -> "WorkoutIn":
		if self.end < self.start:
			raise ValueError("a workout ends after it starts")
		return self


class HealthSync(BaseModel):
	"""Every workout Health has between `since` and `until`: the whole window, so a
	workout deleted in Health is one memo no longer receives, and lets go of."""

	model_config = ConfigDict(extra="forbid")

	since: dt.datetime
	until: dt.datetime
	workouts: list[WorkoutIn] = Field(max_length=MAX_WORKOUTS)

	_window_aware = field_validator("since", "until")(_aware)

	@model_validator(mode="after")
	def _inside(self) -> "HealthSync":
		if self.until <= self.since:
			raise ValueError("until must be after since")
		for w in self.workouts:
			if not self.since <= w.start <= self.until:
				raise ValueError(f"workout {w.id} starts outside the window")
		if len({w.id for w in self.workouts}) != len(self.workouts):
			raise ValueError("a workout is listed twice")
		return self


class HealthSyncOut(BaseModel):
	filed: int  # workouts memo keeps, as activities
	skipped: int  # types memo doesn't count as a habit
	removed: int  # gone from Health since the last sync
