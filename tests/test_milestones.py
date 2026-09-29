"""Milestones (ADR 0050): the numbers the reader takes from a note, and what a habit
reached - a tenth day, a first 10k, the longest run, the heaviest squat - noticed on
read, shown on the habit and, for a couple of days, on Today."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.habits import (
	ActivityFound,
	AmountFound,
	Evidence,
	HabitsExtraction,
	activity_payload,
)
from memo.services.habits import Days, Effort
from memo.services.milestones import milestones
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeHabitsReader,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

TODAY = dt.datetime.now(dt.UTC).date()  # the seed user is on UTC
START = dt.date(2026, 1, 1)


def _ago(days: int) -> dt.date:
	return TODAY - dt.timedelta(days=days)


def _days(
	*efforts: tuple[int, str, float] | tuple[int, str, float, str],
	extra_days: int = 0,
) -> Days:
	"""A habit's days: an effort on START + n for each (n, measure, value[, lift])."""
	days = Days()
	for effort in efforts:
		n, measure, value = effort[0], effort[1], effort[2]
		lift = effort[3] if len(effort) == 4 else ""
		day = START + dt.timedelta(days=n)
		days.add(day, Evidence(kind="logged"))
		days.efforts.append(Effort(day, measure, value, lift, None))
	for n in range(extra_days):
		days.add(START + dt.timedelta(days=200 + n), Evidence(kind="logged"))
	return days


def _titles(key: str, days: Days) -> list[tuple[str, str, dt.date]]:
	later = START + dt.timedelta(days=400)
	return [
		(m.title, m.detail, m.date) for m in milestones(key, "Running", days, later)
	]


# --- the numbers in a note ------------------------------------------------------------


def test_an_amount_is_kept_in_memos_own_units() -> None:
	found = ActivityFound(
		key="running",
		label="10 miles",
		occurred_on=TODAY,
		amounts=[
			AmountFound(measure="distance", value=10, unit="mi"),
			AmountFound(measure="duration", value=1.5, unit="h"),
		],
	)
	gym = ActivityFound(
		key="gym",
		label="squats",
		occurred_on=TODAY,
		amounts=[AmountFound(measure="weight", value=225, unit="lb", lift="Squats!")],
	)

	assert activity_payload(found)["amounts"] == [
		{"measure": "distance", "value": 16.09},
		{"measure": "duration", "value": 90.0},
	]
	assert activity_payload(gym)["amounts"] == [
		{"measure": "weight", "value": 102.06, "lift": "squats"}
	]
	assert "amounts" not in activity_payload(
		ActivityFound(key="gym", label="gym", occurred_on=TODAY)
	)


@pytest.mark.parametrize(
	("amount", "why"),
	[
		({"measure": "distance", "value": 5, "unit": "kg"}, "not a unit of distance"),
		({"measure": "distance", "value": 5000, "unit": "km"}, "not a plausible"),
		({"measure": "weight", "value": 100, "unit": "kg"}, "needs the lift"),
		({"measure": "duration", "value": 30, "unit": "min", "lift": "x"}, "only a"),
		({"measure": "distance", "value": 0, "unit": "km"}, "greater than 0"),
	],
)
def test_an_amount_that_cannot_be_right_goes_back_for_repair(
	amount: dict[str, object], why: str
) -> None:
	with pytest.raises(ValidationError, match=why):
		AmountFound.model_validate(amount)


# --- what counts as a milestone ------------------------------------------------------


def test_every_tenth_and_twenty_fifth_day_is_a_milestone() -> None:
	days = _days(extra_days=25)
	marks = [(m.title, m.date) for m in milestones("gym", "Gym", days, TODAY)]

	assert marks == [
		("25th day", START + dt.timedelta(days=224)),
		("10th day", START + dt.timedelta(days=209)),
	]


def test_a_first_10k_needs_something_shorter_before_it() -> None:
	# A watch's 5k is 4.95 km: close enough.
	grew = _days((0, "distance", 3.0), (7, "distance", 4.95), (14, "distance", 10.2))
	started_long = _days((0, "distance", 10.0), (7, "distance", 6.0))

	firsts = [t for t in _titles("running", grew) if t[0].startswith("First")]
	assert firsts == [
		("First 10k", "10.2 km", START + dt.timedelta(days=14)),
		("First 5k", "5.0 km", START + dt.timedelta(days=7)),
	]
	# Where memo's knowledge starts is not a first.
	assert not [t for t in _titles("running", started_long) if t[0].startswith("First")]


def test_the_longest_run_needs_runs_to_beat_and_a_real_margin() -> None:
	too_few = _days((0, "distance", 5.0), (1, "distance", 6.0), (2, "distance", 9.0))
	record = _days(
		(0, "distance", 5.0),
		(1, "distance", 6.0),
		(2, "distance", 5.5),
		(3, "distance", 12.0),
		(4, "distance", 12.1),  # a hair further: the record's date doesn't move
	)

	assert not [t for t in _titles("running", too_few) if t[0].startswith("Longest")]
	assert ("Longest run yet", "12.0 km", START + dt.timedelta(days=3)) in _titles(
		"running", record
	)


def test_the_heaviest_lift_is_per_lift() -> None:
	days = _days(
		(0, "weight", 80, "squat"),
		(2, "weight", 85, "squat"),
		(4, "weight", 90, "squat"),
		(6, "weight", 100, "squat"),
		(6, "weight", 120, "deadlift"),  # the first deadlift: nothing to beat
	)
	lifted = [(m.title, m.detail) for m in milestones("gym", "Gym", days, TODAY)]

	assert lifted == [("Heaviest squat yet", "100 kg")]


# --- through the app ------------------------------------------------------------------


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


def _ran(km: float, days_ago: int) -> FakeHabitsReader:
	return FakeHabitsReader(
		HabitsExtraction(
			activities=[
				ActivityFound(
					key="running",
					label=f"{km}k",
					occurred_on=_ago(days_ago),
					amounts=[AmountFound(measure="distance", value=km, unit="km")],
				)
			]
		)
	)


async def _note(
	client: AsyncClient, session: AsyncSession, body: str, reader: FakeHabitsReader
) -> str:
	made = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	assert made.status_code == 202, made.text
	cid = uuid.UUID(made.json()["id"])
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
		habits=reader,
	)
	assert (await process_capture(ctx, cid, SEED_USER_ID)).status == "ready"
	return str(cid)


async def test_a_first_10k_from_a_note_is_on_the_habit_and_on_today(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	for km, ago in ((3, 20), (5, 12), (4, 6)):
		await _note(client, session, f"ran {km}k", _ran(km, ago))
	ten = await _note(client, session, "10k along the river!", _ran(10, 1))
	habit = (await client.post("/v1/habits", json={"name": "Running"})).json()

	detail = (await client.get(f"/v1/habits/{habit['id']}")).json()
	firsts = [m for m in detail["milestones"] if m["kind"] == "first"]
	assert [(m["title"], m["capture_id"]) for m in firsts] == [
		("First 10k", ten),
		("First 5k", firsts[1]["capture_id"]),
	]

	today = (await client.get("/v1/morning")).json()
	assert today["milestone"]["title"] == "First 10k"
	assert today["milestone"]["habit_id"] == habit["id"]


async def test_today_keeps_a_milestone_for_a_couple_of_days_and_only_if_tracked(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	for km, ago in ((3, 30), (5, 25), (4, 20), (10, 5)):
		await _note(client, session, f"ran {km}k", _ran(km, ago))

	assert (await client.get("/v1/morning")).json()["milestone"] is None
	await client.post("/v1/habits", json={"name": "Running"})
	# Tracked now, but the 10k was five days ago: on the habit, not on Today.
	assert (await client.get("/v1/morning")).json()["milestone"] is None
