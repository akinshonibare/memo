"""Habits (ADR 0043): the switch, activities read from notes, suggestions, counting by
day, logging and removing a day, family and Bible habits, the nudge, the weekly section,
the backlog, and isolation."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_system, bind_user
from memo.models import Capture
from memo.schemas.derivations import Extraction, TodoPayload
from memo.schemas.habits import ActivityFound, HabitsExtraction
from memo.schemas.people import Contact, MentionPayload, PeopleExtraction
from memo.services.habits import apply_activities, key_for, unread_for_habits
from memo.services.pipeline import (
	PipelineContext,
	process_capture,
	read_habits_in_old_note,
)
from memo.services.retry import RetryableError, TerminalError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeHabitsReader,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

TODAY = dt.datetime.now(dt.UTC).date()  # the seed user is on UTC


def _ago(days: int) -> dt.date:
	return TODAY - dt.timedelta(days=days)


def _did(*found: tuple[str, int]) -> FakeHabitsReader:
	"""A reader that finds each (key, days ago) in the note."""
	return FakeHabitsReader(
		HabitsExtraction(
			activities=[
				ActivityFound(key=key, label=key, occurred_on=_ago(days))
				for key, days in found
			]
		)
	)


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


def _ctx(
	session: AsyncSession,
	habits: FakeHabitsReader,
	*,
	extraction: Extraction | None = None,
	people: PeopleExtraction | None = None,
) -> PipelineContext:
	return PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction or Extraction()),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(people),
		habits=habits,
	)


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	habits: FakeHabitsReader | None = None,
	*,
	extraction: Extraction | None = None,
	people: PeopleExtraction | None = None,
) -> str:
	made = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	assert made.status_code == 202, made.text
	cid = made.json()["id"]
	outcome = await process_capture(
		_ctx(
			session, habits or FakeHabitsReader(), extraction=extraction, people=people
		),
		uuid.UUID(cid),
		SEED_USER_ID,
	)
	assert outcome.status == "ready", outcome
	return str(cid)


async def _habits(client: AsyncClient, on: bool) -> None:
	r = await client.patch("/v1/me", json={"habits_enabled": on})
	assert r.status_code == 200 and r.json()["habits_enabled"] is on


async def _gym_three_days(client: AsyncClient, session: AsyncSession) -> list[str]:
	return [
		await _note(client, session, f"gym session {n}", _did(("gym", n)))
		for n in (1, 3, 5)
	]


async def _track(client: AsyncClient, name: str, **extra: object) -> dict[str, Any]:
	r = await client.post("/v1/habits", json={"name": name, **extra})
	assert r.status_code == 201, r.text
	return dict(r.json())


# --- the switch -----------------------------------------------------------------------


async def test_habits_is_on_for_a_new_account_and_nothing_turns_it_off(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	async with admin_engine.begin() as conn:
		new = uuid.uuid4()
		await conn.execute(
			text("INSERT INTO users (id, timezone) VALUES (:id, 'UTC')"), {"id": new}
		)
		assert await conn.scalar(
			text("SELECT habits_enabled FROM users WHERE id = :id"), {"id": new}
		)
		await conn.execute(text("DELETE FROM users WHERE id = :id"), {"id": new})
	# The seeded account starts switched off (the operator's switch, conftest). An app
	# with the old switch may still send true: accepted, and it's on.
	await _habits(client, True)
	for value in (False, None):
		refused = await client.patch("/v1/me", json={"habits_enabled": value})
		assert refused.status_code == 422, value
	assert (await client.get("/v1/me")).json()["habits_enabled"] is True
	listed = (await client.get("/v1/habits")).json()
	assert listed == {"enabled": True, "habits": [], "suggestion": None}


async def test_switched_off_nothing_is_read_and_no_ledger_row_is_left(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	reader = _did(("gym", 0))
	cid = await _note(client, session, "gym this morning", reader)
	assert reader.calls == 0, "Habits off: no model call"
	async with admin_engine.connect() as conn:
		stages = (
			await conn.scalars(
				text("SELECT stage FROM job_runs WHERE capture_id = :c"), {"c": cid}
			)
		).all()
	# "succeeded" would mean read, and the backlog would never read it once Habits
	# is turned on.
	assert "habits" not in stages
	assert "people" in stages


# --- reading notes --------------------------------------------------------------------


async def test_an_activity_is_filed_from_a_note_and_kept_off_the_note(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	reader = _did(("gym", 0))
	cid = await _note(
		client,
		session,
		"leg day at the gym, then call the landlord",
		reader,
		extraction=Extraction(todos=[TodoPayload(title="call the landlord")]),
	)
	assert reader.calls == 1
	detail = (await client.get(f"/v1/captures/{cid}")).json()
	assert [d["kind"] for d in detail["derivations"]] == ["todo"], (
		"an activity is not an item: it lives on the Habits screen"
	)


async def test_an_activity_after_the_note_was_written_is_refused() -> None:
	# "gym tomorrow" read as done: the date gives it away, and it goes back for repair.
	with pytest.raises(ValidationError, match="after the note"):
		HabitsExtraction.model_validate(
			{
				"activities": [
					{
						"key": "gym",
						"label": "gym tomorrow",
						"occurred_on": str(_ago(-1)),
						"confidence": 0.9,
					}
				]
			},
			context={"captured_on": TODAY},
		)
	parsed = ActivityFound(key="  Leg-Day! ", label="x", occurred_on=TODAY)
	assert parsed.key == "leg day"


async def test_a_reader_failure_never_costs_the_note(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	cid = await _note(
		client,
		session,
		"ran 5k",
		FakeHabitsReader(fail_with=TerminalError("model refused")),
	)
	assert (await client.get(f"/v1/captures/{cid}")).json()["status"] == "ready"


async def test_the_generic_item_routes_never_touch_an_activity(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _habits(client, True)
	await _note(client, session, "ran 5k", _did(("running", 0)))
	async with admin_engine.connect() as conn:
		activity_id = await conn.scalar(
			text("SELECT id FROM derivations WHERE kind = 'activity'")
		)
	patched = await client.patch(
		f"/v1/derivations/{activity_id}", json={"status": "confirmed"}
	)
	assert patched.status_code in (404, 409, 422)


# --- suggestions and tracking ---------------------------------------------------------


async def test_three_days_in_three_weeks_is_a_suggestion_on_today_and_habits(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _note(client, session, "gym", _did(("gym", 1)))
	await _note(client, session, "gym again", _did(("gym", 1)))  # the same day
	await _note(client, session, "gym", _did(("gym", 3)))
	assert (await client.get("/v1/habits")).json()["suggestion"] is None, (
		"two different days is not yet a habit"
	)
	await _note(client, session, "gym", _did(("gym", 5)))
	suggestion = (await client.get("/v1/habits")).json()["suggestion"]
	assert suggestion == {
		"key": "gym",
		"name": "Gym",
		"source": "activity",
		"days": 3,
		"total_days": 3,
	}
	habit = (await client.get("/v1/morning")).json()["habit"]
	assert habit["kind"] == "suggest" and habit["key"] == "gym" and habit["days"] == 3


async def test_old_days_are_not_a_suggestion(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	for n in (25, 30, 40):
		await _note(client, session, "swim", _did(("swimming", n)))
	assert (await client.get("/v1/habits")).json()["suggestion"] is None


async def test_accepting_a_suggestion_brings_its_history_and_counts_days(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _gym_three_days(client, session)
	habit = await _track(client, "Gym", key="gym", target_per_week=3)
	assert habit["total_days"] == 3
	assert habit["last_done"] == str(_ago(1))
	assert habit["since"] == str(_ago(5))
	assert len(habit["weeks"]) == 8 and sum(habit["weeks"]) == 3
	monday = TODAY - dt.timedelta(days=TODAY.weekday())
	assert habit["this_week"] == sum(1 for n in (1, 3, 5) if _ago(n) >= monday)
	assert len(habit["week_days"]) == 7
	listed = (await client.get("/v1/habits")).json()
	assert [h["name"] for h in listed["habits"]] == ["Gym"]
	assert listed["suggestion"] is None, "a tracked habit is not suggested"

	detail = (await client.get(f"/v1/habits/{habit['id']}")).json()
	assert [d["date"] for d in detail["days"]] == [str(_ago(n)) for n in (1, 3, 5)]
	assert all(d["removable"] for d in detail["days"])
	assert detail["days"][0]["evidence"][0]["kind"] == "note"
	assert detail["days"][0]["evidence"][0]["text"] == "gym session 1"


async def test_a_name_memo_knows_is_filed_under_its_key(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _gym_three_days(client, session)
	habit = await _track(client, "Exercise")
	assert habit["key"] == "gym" and habit["name"] == "Exercise"
	assert habit["total_days"] == 3
	assert key_for("Reading the Bible") == "bible"
	assert key_for("Gardening!") == "gardening"


async def test_a_habit_of_your_own_is_given_to_the_reader(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _track(client, "Gardening")
	reader = _did(("gardening", 0))
	await _note(client, session, "an hour in the garden", reader)
	assert reader.extra_keys == ["gardening"]
	(habit,) = (await client.get("/v1/habits")).json()["habits"]
	assert habit["total_days"] == 1


async def test_not_now_dismisses_a_suggestion_for_good(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _gym_three_days(client, session)
	for n in (2, 4, 6):
		await _note(client, session, "read", _did(("reading", n)))
	first = (await client.get("/v1/habits")).json()["suggestion"]["key"]
	r = await client.post("/v1/habits/dismiss", json={"key": first})
	assert r.status_code == 204
	second = (await client.get("/v1/habits")).json()["suggestion"]["key"]
	assert second != first and {first, second} == {"gym", "reading"}
	await client.post("/v1/habits/dismiss", json={"key": second})
	assert (await client.get("/v1/habits")).json()["suggestion"] is None


async def test_stopping_keeps_the_days_and_tracking_again_brings_them_back(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _gym_three_days(client, session)
	habit = await _track(client, "Gym", key="gym")
	assert (await client.delete(f"/v1/habits/{habit['id']}")).status_code == 204
	listed = (await client.get("/v1/habits")).json()
	assert listed["habits"] == [] and listed["suggestion"] is None
	assert (await client.get(f"/v1/habits/{habit['id']}")).status_code == 404
	again = await _track(client, "Gym", key="gym")
	assert again["id"] == habit["id"] and again["total_days"] == 3


async def test_target_and_name_can_change_and_are_validated(
	client: AsyncClient,
) -> None:
	habit = await _track(client, "Reading")
	r = await client.patch(
		f"/v1/habits/{habit['id']}", json={"target_per_week": 4, "name": "  Books "}
	)
	assert r.json()["target_per_week"] == 4 and r.json()["name"] == "Books"
	cleared = await client.patch(
		f"/v1/habits/{habit['id']}", json={"target_per_week": None}
	)
	assert cleared.json()["target_per_week"] is None
	for bad in ({"target_per_week": 8}, {"target_per_week": 0}, {}):
		assert (
			await client.patch(f"/v1/habits/{habit['id']}", json=bad)
		).status_code == 422
	assert (await client.post("/v1/habits", json={"name": "   "})).status_code == 422
	assert (await client.post("/v1/habits", json={"name": "123"})).status_code == 422


# --- logging and removing days --------------------------------------------------------


async def test_log_today_counts_once_and_never_a_future_day(
	client: AsyncClient,
) -> None:
	habit = await _track(client, "Meditation")
	url = f"/v1/habits/{habit['id']}/log"
	assert (await client.post(url, json={})).json()["total_days"] == 1
	assert (await client.post(url, json={})).json()["total_days"] == 1
	earlier = await client.post(url, json={"date": str(_ago(2))})
	assert earlier.json()["total_days"] == 2
	assert (await client.post(url, json={"date": str(_ago(-1))})).status_code == 422
	detail = (await client.get(f"/v1/habits/{habit['id']}")).json()
	(today,) = detail["days"][0]["evidence"]
	assert today["kind"] == "logged" and detail["days"][0]["removable"]


async def test_a_removed_day_stays_removed_when_the_note_is_read_again(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	cids = await _gym_three_days(client, session)
	habit = await _track(client, "Gym", key="gym")
	removed = await client.delete(f"/v1/habits/{habit['id']}/days/{_ago(3)}")
	assert removed.json()["total_days"] == 2

	# The same note read again by the same reader version: the day doesn't come back.
	capture = await session.scalar(
		select(Capture).where(Capture.id == uuid.UUID(cids[1]))
	)
	assert capture is not None
	await apply_activities(
		session,
		capture,
		HabitsExtraction(
			activities=[ActivityFound(key="gym", label="gym", occurred_on=_ago(3))]
		),
		version="habits-v1/fake",
	)
	await session.commit()
	await bind_user(session, SEED_USER_ID)
	after = (await client.get(f"/v1/habits/{habit['id']}")).json()
	assert after["total_days"] == 2
	assert str(_ago(3)) not in [d["date"] for d in after["days"]]


# --- family and the Bible -------------------------------------------------------------


async def _person(admin: AsyncEngine, name: str, note: str | None = None) -> str:
	pid = uuid.uuid4()
	async with admin.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO people (id, user_id, name, note) "
				"VALUES (:id, :u, :n, :note)"
			),
			{"id": pid, "u": SEED_USER_ID, "n": name, "note": note},
		)
	return str(pid)


def _met(name: str, days: int, contact: Contact = "call") -> PeopleExtraction:
	return PeopleExtraction(
		people=[MentionPayload(name=name, contact=contact, occurred_on=_ago(days))]
	)


async def test_time_with_family_is_counted_from_people(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _habits(client, True)
	mum = await _person(admin_engine, "Mum")
	await _person(admin_engine, "Tunde")
	await _note(client, session, "called Mum", people=_met("Mum", 1))
	await _note(client, session, "lunch with Mum", people=_met("Mum", 4, "in_person"))
	await _note(client, session, "texted Tunde", people=_met("Tunde", 2, "message"))
	await _note(client, session, "Mum wants to visit", people=_met("Mum", 6, "none"))
	habit = await _track(client, "Talking to family")
	assert habit["key"] == "family" and habit["source"] == "family"
	assert habit["total_days"] == 2, (
		"Tunde isn't family; 'wants to visit' isn't contact"
	)
	detail = (await client.get(f"/v1/habits/{habit['id']}")).json()
	assert [e["text"] for d in detail["days"] for e in d["evidence"]] == [
		"Called Mum",
		"Saw Mum",
	]
	assert not any(d["removable"] for d in detail["days"]), "changed in People"

	people = {p["name"]: p for p in (await client.get("/v1/people")).json()["people"]}
	assert people["Mum"]["family"] is True and people["Tunde"]["family"] is False
	await client.patch(f"/v1/people/{mum}", json={"family": False})
	assert (await client.get(f"/v1/habits/{habit['id']}")).json()["total_days"] == 0
	await client.patch(f"/v1/people/{mum}", json={"family": None})
	assert (await client.get(f"/v1/habits/{habit['id']}")).json()["total_days"] == 2


async def test_a_cousin_is_family_when_the_note_says_so(
	admin_engine: AsyncEngine, client: AsyncClient
) -> None:
	await _person(admin_engine, "Dayo", note="my cousin")
	await _person(admin_engine, "Sam", note="from work")
	people = {p["name"]: p for p in (await client.get("/v1/people")).json()["people"]}
	assert people["Dayo"]["family"] is True and people["Sam"]["family"] is False


async def test_reading_the_bible_counts_faith_readings_and_notes(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await client.post("/v1/faith/readings", json={"ref": "John 3"})
	await _note(client, session, "read my Bible", _did(("bible", 2)))
	habit = await _track(client, "Bible reading")
	assert habit["key"] == "bible" and habit["source"] == "bible"
	assert habit["total_days"] == 2
	detail = (await client.get(f"/v1/habits/{habit['id']}")).json()
	kinds = [e["kind"] for d in detail["days"] for e in d["evidence"]]
	assert sorted(kinds) == ["note", "reading"]


# --- the nudge and the week -----------------------------------------------------------


async def test_a_quiet_habit_with_a_target_gets_one_gentle_nudge(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _habits(client, True)
	await _note(client, session, "ran", _did(("running", 7)))
	habit = await _track(client, "Running", key="running")
	assert (await client.get("/v1/morning")).json()["habit"] is None, "no target"
	await client.patch(f"/v1/habits/{habit['id']}", json={"target_per_week": 3})
	nudge = (await client.get("/v1/morning")).json()["habit"]
	assert nudge["kind"] == "nudge" and nudge["habit_id"] == habit["id"]
	assert nudge["last_done"] == str(_ago(7))
	await client.post(f"/v1/habits/{habit['id']}/log", json={})
	assert (await client.get("/v1/morning")).json()["habit"] is None


async def test_the_weekly_review_has_a_habits_section_only_while_on(
	client: AsyncClient,
) -> None:
	habit = await _track(client, "Yoga", target_per_week=2)
	await client.post(f"/v1/habits/{habit['id']}/log", json={})
	assert (await client.get("/v1/review/week")).json()["habits"] is None
	await _habits(client, True)
	(week,) = (await client.get("/v1/review/week")).json()["habits"]
	assert week == {
		"habit_id": habit["id"],
		"name": "Yoga",
		"days": 1,
		"target_per_week": 2,
	}


# --- the backlog ----------------------------------------------------------------------


async def test_turning_habits_on_reads_the_last_sixty_days(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
) -> None:
	recent = await _note(client, session, "gym")
	old = await _note(client, session, "gym long ago")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE captures SET created_at = now() - interval '90 days' "
				"WHERE id = :c"
			),
			{"c": old},
		)

	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_for_habits(s, limit=10) == [], "off: nothing to read"
	await _habits(client, True)
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_for_habits(s, limit=10) == [
			(uuid.UUID(recent), SEED_USER_ID)
		]

	flaky = FakeHabitsReader(fail_with=RetryableError("429"))
	assert (
		await read_habits_in_old_note(
			_ctx(session, flaky), uuid.UUID(recent), SEED_USER_ID
		)
		== "retry_later"
	)
	reader = _did(("gym", 0))
	ctx = _ctx(session, reader)
	assert await read_habits_in_old_note(ctx, uuid.UUID(recent), SEED_USER_ID) == "read"
	assert await read_habits_in_old_note(ctx, uuid.UUID(recent), SEED_USER_ID) == "done"
	assert reader.calls == 1
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_for_habits(s, limit=10) == []


# --- export, deletion, isolation ------------------------------------------------------


async def test_export_carries_habits_and_delete_removes_them(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	habit = await _track(client, "Journalling", target_per_week=5)
	await client.post(f"/v1/habits/{habit['id']}/log", json={})
	exported = (await client.get("/v1/me/export")).json()
	assert [(h["key"], h["target_per_week"]) for h in exported["habits"]] == [
		("journalling", 5)
	]
	wiped = await client.delete(f"/v1/me/data?confirm={SEED_USER_ID}")
	assert wiped.json()["deleted"]["habits"] == 1
	async with admin_engine.connect() as conn:
		assert await conn.scalar(text("SELECT count(*) FROM habits")) == 0


async def test_habits_are_the_users_own(
	client: AsyncClient,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	habit = await _track(client, "Cooking")
	as_user(second_user)
	assert (await client.get("/v1/habits")).json()["habits"] == []
	for method, path, body in (
		("GET", f"/v1/habits/{habit['id']}", None),
		("PATCH", f"/v1/habits/{habit['id']}", {"target_per_week": 2}),
		("POST", f"/v1/habits/{habit['id']}/log", {}),
		("DELETE", f"/v1/habits/{habit['id']}/days/{TODAY}", None),
		("DELETE", f"/v1/habits/{habit['id']}", None),
	):
		r = await client.request(method, path, json=body)
		assert r.status_code == 404, (method, path, r.status_code)
	mine = await _track(client, "Cooking")
	assert mine["id"] != habit["id"] and mine["total_days"] == 0
