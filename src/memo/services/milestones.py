"""Milestones (ADR 0050): what a habit reached, noticed from the days and numbers memo
already has - never logged, never stored.

A milestone is computed on every read, like a habit's counts (ADR 0043): a stored one
would have to follow every removed day and every workout deleted in Apple Health.

Four kinds, each shown once, dated the day it happened:

- **days**: the 10th, 25th, 50th, 100th ... day a habit counted;
- **first**: a named distance reached for the first time ("First 10k");
- **longest**: the furthest run, walk, ride or swim so far - only once there are a few
  before it to beat, so the second run ever isn't a record;
- **heaviest**: the heaviest weight for one lift so far, on the same terms.

"First" means the first memo knows of. With Apple Health on, that's years of workouts;
from notes alone, it's what you've said since you turned Habits on.
"""

import datetime as dt
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from memo.models import Habit, User
from memo.schemas.habits import HabitPrompt, Milestone, TodayMilestone
from memo.services.habits import (
	Days,
	Effort,
	habits_on,
	load_sources,
	nudge,
	suggestion,
	today_for,
	tracked,
)

DAY_MARKS = (10, 25, 50, 100, 150, 200, 300, 365, 500, 750, 1000)
# Distances worth a name, per habit, in km. A watch's 5k is often 4.95 km, so a
# distance counts from 98% of the mark.
FIRSTS: dict[str, tuple[tuple[float, str], ...]] = {
	"running": (
		(5.0, "First 5k"),
		(10.0, "First 10k"),
		(21.0975, "First half marathon"),
		(42.195, "First marathon"),
	),
	"walking": ((10.0, "First 10 km walk"), (20.0, "First 20 km walk")),
	"cycling": ((50.0, "First 50 km ride"), (100.0, "First 100 km ride")),
	"swimming": ((1.0, "First 1 km swim"), (5.0, "First 5 km swim")),
}
CLOSE_ENOUGH = 0.98
NOUNS = {"running": "run", "walking": "walk", "cycling": "ride", "swimming": "swim"}
# A record needs something to beat: this many before it, and a real margin.
BEFORE_A_RECORD = 3
RECORD_MARGIN = 1.02
# How long a milestone stays on Today.
FRESH_DAYS = 2

# Which kind wins Today when two land on the same day.
_RANK = {"first": 0, "heaviest": 1, "longest": 2, "days": 3}


def _ordinal(n: int) -> str:
	suffix = (
		"th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
	)
	return f"{n}{suffix}"


def _km(value: float) -> str:
	return f"{value:.1f} km" if value < 100 else f"{value:.0f} km"


def _kg(value: float) -> str:
	return f"{value:g} kg"


def _in_order(efforts: Iterable[Effort], measure: str) -> list[Effort]:
	return sorted(
		(e for e in efforts if e.measure == measure), key=lambda e: (e.day, -e.value)
	)


def _day_marks(name: str, days: Days, today: dt.date) -> list[Milestone]:
	dates = sorted(d for d in days.dates if d <= today)
	return [
		Milestone(
			kind="days",
			title=f"{_ordinal(mark)} day",
			detail=name,
			date=dates[mark - 1],
		)
		for mark in DAY_MARKS
		if mark <= len(dates)
	]


def _firsts(key: str, distances: list[Effort]) -> list[Milestone]:
	"""Each named distance, the first time memo saw it reached - and only when memo
	also saw something shorter before it. A 5k as the very first run memo knows of is
	where its knowledge starts, not a first."""
	out = []
	for mark, title in FIRSTS.get(key, ()):
		reached = [i for i, e in enumerate(distances) if e.value >= mark * CLOSE_ENOUGH]
		if not reached or reached[0] == 0:
			continue
		first = distances[reached[0]]
		out.append(
			Milestone(
				kind="first",
				title=title,
				detail=_km(first.value),
				date=first.day,
				capture_id=first.capture_id,
			)
		)
	return out


def _record(efforts: list[Effort]) -> Effort | None:
	"""The effort that set the standing record: the first to come within a whisker
	of the best there is, provided it had enough before it and beat them clearly."""
	if len(efforts) <= BEFORE_A_RECORD:
		return None
	top = max(e.value for e in efforts)
	i = next(i for i, e in enumerate(efforts) if e.value * RECORD_MARGIN >= top)
	if i < BEFORE_A_RECORD:
		return None
	before = max(e.value for e in efforts[:i])
	return efforts[i] if efforts[i].value > before * RECORD_MARGIN else None


def _longest(key: str, name: str, distances: list[Effort]) -> list[Milestone]:
	record = _record(distances)
	if record is None:
		return []
	noun = NOUNS.get(key)
	return [
		Milestone(
			kind="longest",
			title=f"Longest {noun} yet" if noun else f"Furthest {name.lower()} yet",
			detail=_km(record.value),
			date=record.day,
			capture_id=record.capture_id,
		)
	]


def _heaviest(weights: list[Effort]) -> list[Milestone]:
	out = []
	for lift in sorted({e.lift for e in weights if e.lift}):
		record = _record([e for e in weights if e.lift == lift])
		if record is not None:
			out.append(
				Milestone(
					kind="heaviest",
					title=f"Heaviest {lift} yet",
					detail=_kg(record.value),
					date=record.day,
					capture_id=record.capture_id,
				)
			)
	return out


def milestones(key: str, name: str, days: Days, today: dt.date) -> list[Milestone]:
	"""Everything a habit has reached, newest first."""
	efforts = [e for e in days.efforts if e.day <= today]
	distances = _in_order(efforts, "distance")
	found = [
		*_day_marks(name, days, today),
		*_firsts(key, distances),
		*_longest(key, name, distances),
		*_heaviest(_in_order(efforts, "weight")),
	]
	return sorted(found, key=lambda m: (m.date, -_RANK[m.kind]), reverse=True)


def fresh_milestone(
	habits: list[Habit], sources_days: dict[str, Days], today: dt.date
) -> TodayMilestone | None:
	"""The one milestone for Today: the newest from the last couple of days, across
	the habits you track."""
	floor = today - dt.timedelta(days=FRESH_DAYS)
	best: tuple[tuple[dt.date, int], TodayMilestone] | None = None
	for habit in habits:
		if habit.status != "active":
			continue
		for m in milestones(habit.key, habit.name, sources_days[habit.key], today):
			if m.date < floor:
				break  # newest first: the rest are older
			rank = (m.date, -_RANK[m.kind])
			if best is None or rank > best[0]:
				best = (
					rank,
					TodayMilestone(
						**m.model_dump(), habit_id=habit.id, name=habit.name
					),
				)
	return best[1] if best else None


async def todays_habits(
	session: AsyncSession, user: User, *, now: dt.datetime | None = None
) -> tuple[HabitPrompt | None, TodayMilestone | None]:
	"""Today's habit lines, read once: the suggestion or nudge (ADR 0043), and a
	milestone reached in the last couple of days."""
	if not habits_on(user):
		return None, None
	today = today_for(user, now)
	habits = await tracked(session, user.id)
	sources = await load_sources(session, user)
	found = suggestion(habits, sources, today)
	prompt = (
		HabitPrompt(
			kind="suggest",
			key=found.key,
			name=found.name,
			source=found.source,
			days=found.days,
		)
		if found is not None
		else nudge(habits, sources, today)
	)
	milestone = fresh_milestone(
		habits, {h.key: sources.days_for(h.key) for h in habits}, today
	)
	return prompt, milestone
