"""The morning view: today's items, one resurfaced idea, and DST-safe scheduling."""

import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator, Callable
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user, commit_and_rebind
from memo.jobs import build_morning_views_task
from memo.services.morning import (
	BUILD_HOUR,
	build_morning_view,
	is_build_hour,
	look_back_days,
	todays_view,
)

LONDON = ZoneInfo("Europe/London")
NOW = dt.datetime(2026, 9, 28, 3, 30, tzinfo=dt.UTC)  # 04:30 in London (BST)


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _capture(client: AsyncClient, token: str) -> str:
	created = await client.post(
		"/v1/captures", json={"text": "seed", "client_token": token}
	)
	capture_id: str = created.json()["id"]
	return capture_id


async def _derivation(
	admin_engine: AsyncEngine,
	capture_id: str,
	*,
	kind: str,
	payload: dict[str, object],
	created_at: dt.datetime,
	status: str = "confirmed",
	user_id: uuid.UUID = SEED_USER_ID,
) -> str:
	derivation_id = uuid.uuid4()
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO derivations (id, user_id, capture_id, kind, payload, "
				"status, extractor_version, created_at) VALUES (:id, :user_id, "
				":capture_id, :kind, CAST(:payload AS jsonb), :status, 'test', "
				":created)"
			),
			{
				"id": derivation_id,
				"user_id": user_id,
				"capture_id": capture_id,
				"kind": kind,
				"payload": json.dumps(payload),
				"status": status,
				"created": created_at,
			},
		)
	return str(derivation_id)


async def _set_timezone(admin_engine: AsyncEngine, tz: str) -> None:
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("UPDATE users SET timezone = :tz WHERE id = :id"),
			{"tz": tz, "id": SEED_USER_ID},
		)


async def test_view_holds_todays_items_and_one_old_idea(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	await _set_timezone(admin_engine, "Europe/London")
	cap = await _capture(client, "tok-morn-1")
	today = NOW.astimezone(LONDON).date()
	old = NOW - dt.timedelta(days=30)
	await _derivation(
		admin_engine,
		cap,
		kind="todo",
		payload={"title": "Call Tunde", "due": today.isoformat()},
		created_at=old,
	)
	await _derivation(
		admin_engine,
		cap,
		kind="todo",
		payload={
			"title": "Yesterday's, still open",
			"due": (today - dt.timedelta(days=1)).isoformat(),
		},
		created_at=old,
	)
	await _derivation(
		admin_engine,
		cap,
		kind="todo",
		payload={
			"title": "Tomorrow",
			"due": (today + dt.timedelta(days=1)).isoformat(),
		},
		created_at=old,
	)
	await _derivation(
		admin_engine, cap, kind="todo", payload={"title": "Undated"}, created_at=old
	)
	await _derivation(
		admin_engine,
		cap,
		kind="event",
		payload={
			"title": "Dentist",
			"starts_at": f"{today.isoformat()}T14:30:00+01:00",
		},
		created_at=old,
	)
	await _derivation(
		admin_engine,
		cap,
		kind="event",
		payload={
			"title": "Next week",
			"starts_at": f"{(today + dt.timedelta(days=7)).isoformat()}T09:00:00+01:00",
		},
		created_at=old,
	)
	old_idea = await _derivation(
		admin_engine,
		cap,
		kind="idea",
		payload={"title": "Ration resurfacing", "summary": "One a day."},
		created_at=old,
	)
	await _derivation(
		admin_engine,
		cap,
		kind="idea",
		payload={"title": "Too fresh", "summary": "x"},
		created_at=NOW - dt.timedelta(hours=5),
	)
	await _derivation(
		admin_engine,
		cap,
		kind="idea",
		payload={"title": "Rejected", "summary": "x"},
		created_at=old,
		status="rejected",
	)

	view = await build_morning_view(session, SEED_USER_ID, now=NOW)
	await commit_and_rebind(session, SEED_USER_ID)

	assert view.local_date == today and view.timezone == "Europe/London"
	assert [t.title for t in view.todos] == ["Yesterday's, still open", "Call Tunde"]
	assert [e.title for e in view.events] == ["Dentist"]
	assert view.resurfaced_idea is not None
	assert str(view.resurfaced_idea.id) == old_idea
	assert view.resurfaced_idea.captured_on == old.astimezone(LONDON).date()


async def test_rebuilding_the_same_day_keeps_the_same_idea_and_never_repeats_it_soon(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	cap = await _capture(client, "tok-morn-2")
	old = NOW - dt.timedelta(days=40)
	first = await _derivation(
		admin_engine,
		cap,
		kind="idea",
		payload={"title": "Older", "summary": "a"},
		created_at=old,
	)
	second = await _derivation(
		admin_engine,
		cap,
		kind="idea",
		payload={"title": "Newer", "summary": "b"},
		created_at=old + dt.timedelta(days=10),
	)

	day1 = await build_morning_view(session, SEED_USER_ID, now=NOW)
	await commit_and_rebind(session, SEED_USER_ID)
	day1_again = await build_morning_view(
		session, SEED_USER_ID, now=NOW + dt.timedelta(hours=6)
	)
	await commit_and_rebind(session, SEED_USER_ID)
	day2 = await build_morning_view(
		session, SEED_USER_ID, now=NOW + dt.timedelta(days=1)
	)
	await commit_and_rebind(session, SEED_USER_ID)
	day3 = await build_morning_view(
		session, SEED_USER_ID, now=NOW + dt.timedelta(days=2)
	)
	await commit_and_rebind(session, SEED_USER_ID)

	assert day1.resurfaced_idea is not None and str(day1.resurfaced_idea.id) == first
	assert day1_again.resurfaced_idea is not None
	assert day1_again.resurfaced_idea.id == day1.resurfaced_idea.id
	assert day2.resurfaced_idea is not None and str(day2.resurfaced_idea.id) == second
	assert day3.resurfaced_idea is None, "both shown within the cooldown: nothing"
	async with admin_engine.connect() as conn:
		shown = await conn.scalar(text("SELECT count(*) FROM resurfacings"))
	assert shown == 2


async def test_reads_after_the_build_see_reviews_and_additions(
	app: FastAPI, client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	# ADR 0020: the idea is pinned at 04:00, todos and events are read live.
	await _set_timezone(admin_engine, "Europe/London")
	today = NOW.astimezone(LONDON).date()
	old = NOW - dt.timedelta(days=30)
	cap = await _capture(client, "tok-morn-live")
	idea_cap = await _capture(client, "tok-morn-live-idea")
	dropped = await _derivation(
		admin_engine,
		cap,
		kind="todo",
		payload={"title": "Rejected later", "due": today.isoformat()},
		created_at=old,
		status="pending_review",
	)
	idea = await _derivation(
		admin_engine,
		idea_cap,
		kind="idea",
		payload={"title": "Rejected later", "summary": "x"},
		created_at=old,
	)
	built = await build_morning_view(session, SEED_USER_ID, now=NOW)
	await commit_and_rebind(session, SEED_USER_ID)
	assert [t.title for t in built.todos] == ["Rejected later"]
	assert built.resurfaced_idea is not None and str(built.resurfaced_idea.id) == idea

	await client.patch(f"/v1/derivations/{dropped}", json={"status": "rejected"})
	await _derivation(
		admin_engine,
		cap,
		kind="todo",
		payload={"title": "Added at lunch", "due": today.isoformat()},
		created_at=NOW + dt.timedelta(hours=8),
		status="user_created",
	)
	await client.patch(f"/v1/derivations/{idea}", json={"status": "rejected"})

	async with app.state.session_factory() as fresh:
		await bind_user(fresh, SEED_USER_ID)
		later = await todays_view(fresh, SEED_USER_ID, now=NOW + dt.timedelta(hours=9))

	assert later is not None
	assert [t.title for t in later.todos] == ["Added at lunch"]
	assert later.resurfaced_idea is None
	assert later.built_at == built.built_at, "a read never rewrites the snapshot"


async def test_build_hour_fires_exactly_once_a_day_across_dst_changes() -> None:
	# Europe/London leaves BST on 2026-10-25 and enters it on 2026-03-29.
	for start in (
		dt.datetime(2026, 10, 24, tzinfo=dt.UTC),
		dt.datetime(2026, 3, 28, tzinfo=dt.UTC),
	):
		for day in range(3):
			hits = [
				h
				for h in range(24)
				if is_build_hour(
					start + dt.timedelta(days=day, hours=h), "Europe/London"
				)
			]
			assert len(hits) == 1, (start, day, hits)
	assert is_build_hour(dt.datetime(2026, 9, 28, 3, 5, tzinfo=dt.UTC), "Europe/London")
	assert is_build_hour(dt.datetime(2026, 12, 1, 4, 5, tzinfo=dt.UTC), "Europe/London")
	assert is_build_hour(dt.datetime(2026, 9, 28, 4, 5, tzinfo=dt.UTC), "not/a-zone")


async def test_hourly_task_builds_only_users_in_their_build_hour(
	app: FastAPI, admin_engine: AsyncEngine, second_user: uuid.UUID
) -> None:
	await _set_timezone(admin_engine, "Europe/London")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("UPDATE users SET timezone = 'America/New_York' WHERE id = :id"),
			{"id": second_user},
		)
	ctx = {"session_factory": app.state.session_factory}

	built = await build_morning_views_task(ctx, now=NOW)  # 04:30 London, 23:30 NY

	assert built == 1
	async with admin_engine.connect() as conn:
		rows = (await conn.execute(text("SELECT user_id FROM morning_views"))).all()
	assert [r[0] for r in rows] == [SEED_USER_ID]
	assert BUILD_HOUR == 4


async def test_endpoint_builds_on_demand_and_is_per_user(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	cap = await _capture(client, "tok-morn-3")
	today = dt.datetime.now(dt.UTC).date()
	await _derivation(
		admin_engine,
		cap,
		kind="todo",
		payload={"title": "Only mine", "due": today.isoformat()},
		created_at=NOW,
	)

	mine = (await client.get("/v1/morning")).json()
	assert [t["title"] for t in mine["todos"]] == ["Only mine"]
	assert mine["resurfaced_idea"] is None

	as_user(second_user)
	theirs = (await client.get("/v1/morning")).json()
	assert theirs["todos"] == [] and theirs["events"] == []


# --- on this day (ADR 0040) -----------------------------------------------------------


async def _note_on(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	token: str,
	body: str,
	written: dt.datetime,
	*,
	status: str = "ready",
) -> str:
	"""A note as if written at `written` and already processed."""
	capture_id = await _capture(client, token)
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE captures SET text = :body, status = :status, created_at = :at "
				"WHERE id = :id"
			),
			{"body": body, "status": status, "at": written, "id": capture_id},
		)
	return capture_id


def test_look_back_days_prefers_years_then_six_months_then_one() -> None:
	days = look_back_days(dt.date(2026, 9, 29))
	assert days[0] == dt.date(2016, 9, 29)
	assert days[9] == dt.date(2025, 9, 29)
	assert days[10:] == [dt.date(2026, 3, 29), dt.date(2026, 8, 29)]


def test_days_that_dont_exist_are_skipped_not_moved() -> None:
	# 29 February only has a match in leap years; 31 March has no 31 September six
	# months back and no 31 February one month back, so only the years remain.
	leap = look_back_days(dt.date(2028, 2, 29))
	assert leap == [
		dt.date(2020, 2, 29),
		dt.date(2024, 2, 29),
		dt.date(2027, 8, 29),
		dt.date(2028, 1, 29),
	]
	march = look_back_days(dt.date(2026, 3, 31))
	assert march == [dt.date(y, 3, 31) for y in range(2016, 2026)]


async def test_one_note_from_this_day_the_furthest_back_first(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	await _set_timezone(admin_engine, "Europe/London")
	today = NOW.astimezone(LONDON).date()
	noon = dt.time(12, tzinfo=LONDON)
	year_ago = dt.datetime.combine(today.replace(year=today.year - 1), noon)
	two_years = dt.datetime.combine(today.replace(year=today.year - 2), noon)
	month_ago = dt.datetime.combine(today.replace(month=today.month - 1), noon)
	await _note_on(client, admin_engine, "tok-otd-1", "a year ago", year_ago)
	older = await _note_on(
		client, admin_engine, "tok-otd-2", "two years ago, and longer", two_years
	)
	await _note_on(client, admin_engine, "tok-otd-3", "a month ago", month_ago)
	await _note_on(
		client,
		admin_engine,
		"tok-otd-4",
		"the day before, a year ago",
		year_ago - dt.timedelta(days=1),
	)

	view = await build_morning_view(session, SEED_USER_ID, now=NOW)
	await commit_and_rebind(session, SEED_USER_ID)

	assert view.on_this_day is not None
	assert str(view.on_this_day.capture_id) == older
	assert view.on_this_day.captured_on == two_years.date()
	assert view.on_this_day.text == "two years ago, and longer"


async def test_on_this_day_reads_the_local_date_not_utc(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	await _set_timezone(admin_engine, "Europe/London")
	today = NOW.astimezone(LONDON).date()
	# 00:30 in London, a year ago, is still the previous day in UTC.
	just_after_midnight = dt.datetime.combine(
		today.replace(year=today.year - 1), dt.time(0, 30, tzinfo=LONDON)
	)
	capture_id = await _note_on(
		client, admin_engine, "tok-otd-local", "late night note", just_after_midnight
	)
	view = await build_morning_view(session, SEED_USER_ID, now=NOW)
	assert (
		view.on_this_day is not None and str(view.on_this_day.capture_id) == capture_id
	)


async def test_on_this_day_ends_at_local_midnight(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	await _set_timezone(admin_engine, "Europe/London")
	today = NOW.astimezone(LONDON).date()
	year_ago = today.replace(year=today.year - 1)
	await build_morning_view(session, SEED_USER_ID, now=NOW)
	await commit_and_rebind(session, SEED_USER_ID)
	# 00:30 the next local day is the day after, though still that day in UTC.
	next_day = dt.datetime.combine(
		year_ago + dt.timedelta(days=1), dt.time(0, 30, tzinfo=LONDON)
	)
	await _note_on(client, admin_engine, "tok-otd-after", "the day after", next_day)
	view = await todays_view(session, SEED_USER_ID, now=NOW)
	assert view is not None and view.on_this_day is None

	late = dt.datetime.combine(year_ago, dt.time(23, 30, tzinfo=LONDON))
	capture_id = await _note_on(
		client, admin_engine, "tok-otd-late", "late that day", late
	)
	view = await todays_view(session, SEED_USER_ID, now=NOW)
	assert view is not None and view.on_this_day is not None
	assert str(view.on_this_day.capture_id) == capture_id
	assert view.on_this_day.captured_on == year_ago


async def test_unfinished_empty_and_other_peoples_notes_never_look_back(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	today = NOW.date()
	year_ago = dt.datetime.combine(
		today.replace(year=today.year - 1), dt.time(12, tzinfo=dt.UTC)
	)
	await _note_on(
		client, admin_engine, "tok-otd-f", "failed", year_ago, status="failed"
	)
	await _note_on(
		client, admin_engine, "tok-otd-p", "processing", year_ago, status="extracting"
	)
	await _note_on(client, admin_engine, "tok-otd-e", "   ", year_ago)
	as_user(second_user)
	await _note_on(client, admin_engine, "tok-otd-theirs", "not yours", year_ago)
	as_user(SEED_USER_ID)

	view = await build_morning_view(session, SEED_USER_ID, now=NOW)
	assert view.on_this_day is None


async def test_the_live_read_looks_back_too_and_follows_a_delete(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	today = NOW.date()
	year_ago = dt.datetime.combine(
		today.replace(year=today.year - 1), dt.time(12, tzinfo=dt.UTC)
	)
	await build_morning_view(session, SEED_USER_ID, now=NOW)
	await commit_and_rebind(session, SEED_USER_ID)
	capture_id = await _note_on(
		client, admin_engine, "tok-otd-live", "remember this", year_ago
	)

	view = await todays_view(session, SEED_USER_ID, now=NOW)
	assert view is not None and view.on_this_day is not None
	assert str(view.on_this_day.capture_id) == capture_id

	assert (await client.delete(f"/v1/captures/{capture_id}")).status_code == 204
	view = await todays_view(session, SEED_USER_ID, now=NOW)
	assert view is not None and view.on_this_day is None
