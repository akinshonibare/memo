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
