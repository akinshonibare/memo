"""Apple Health (ADR 0050): workouts synced from the phone become activities - a habit's
days, its milestones, removable like any day - one window at a time, idempotently, and
only while Habits is on."""

import datetime as dt
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID

NOW = dt.datetime.now(dt.UTC).replace(microsecond=0)
SINCE = NOW - dt.timedelta(days=60)

RUN, SQUASH_TYPE, GOLF, STRENGTH = 37, 43, 21, 50


def _workout(
	days_ago: int,
	*,
	kind: int = RUN,
	km: float | None = 5.2,
	minutes: float = 28,
	id: str | None = None,
) -> dict[str, Any]:
	start = NOW - dt.timedelta(days=days_ago, hours=1)
	return {
		"id": id or str(uuid.uuid4()).upper(),
		"activity_type": kind,
		"start": start.isoformat(),
		"end": (start + dt.timedelta(minutes=minutes)).isoformat(),
		"duration_s": minutes * 60,
		"distance_m": None if km is None else km * 1000,
	}


async def _sync(
	client: AsyncClient,
	workouts: list[dict[str, Any]],
	*,
	since: dt.datetime = SINCE,
	expect: int = 200,
) -> dict[str, Any]:
	r = await client.put(
		"/v1/workouts",
		json={
			"since": since.isoformat(),
			"until": NOW.isoformat(),
			"workouts": workouts,
		},
	)
	assert r.status_code == expect, r.text
	return dict(r.json()) if r.content else {}


async def _running(client: AsyncClient) -> dict[str, Any]:
	habit = (await client.post("/v1/habits", json={"name": "Running"})).json()
	return dict((await client.get(f"/v1/habits/{habit['id']}")).json())


async def _health_rows(admin: AsyncEngine, user: uuid.UUID = SEED_USER_ID) -> int:
	async with admin.connect() as conn:
		return int(
			await conn.scalar(
				text(
					"SELECT count(*) FROM derivations WHERE user_id = :u "
					"AND payload ? 'health_id'"
				),
				{"u": user},
			)
			or 0
		)


async def test_workouts_are_read_only_while_habits_is_on(client: AsyncClient) -> None:
	await _sync(client, [_workout(1)], expect=409)
	await client.patch("/v1/me", json={"habits_enabled": True})
	assert (await _sync(client, [_workout(1)]))["filed"] == 1


async def test_a_workout_counts_a_day_with_its_numbers_and_the_rest_are_left(
	client: AsyncClient,
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	out = await _sync(
		client,
		[
			_workout(1),
			_workout(3, kind=STRENGTH, km=0.4, minutes=50),
			_workout(2, kind=GOLF, km=6),
		],
	)
	assert out == {"filed": 2, "skipped": 1, "removed": 0}

	running = await _running(client)
	(day,) = running["days"]
	assert day["removable"] is True
	assert day["evidence"] == [
		{
			"kind": "health",
			"text": "Run · 5.2 km · 28 min",
			"capture_id": None,
			"activity_id": day["evidence"][0]["activity_id"],
		}
	]
	gym = (await client.post("/v1/habits", json={"name": "Gym"})).json()
	(gym_day,) = (await client.get(f"/v1/habits/{gym['id']}")).json()["days"]
	# An elliptical or a weights session has no distance that means anything.
	assert gym_day["evidence"][0]["text"] == "Strength training · 50 min"


async def test_the_same_window_twice_changes_nothing(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	window = [_workout(1), _workout(4), _workout(9)]
	await _sync(client, window)
	again = await _sync(client, window)

	assert again == {"filed": 3, "skipped": 0, "removed": 0}
	assert await _health_rows(admin_engine) == 3
	assert (await _running(client))["total_days"] == 3


async def test_a_workout_gone_from_health_goes_but_only_inside_the_window(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	old, recent, kept = _workout(40), _workout(10), _workout(2)
	await _sync(client, [old, recent, kept])

	# A later pass reads the last 30 days: the 10-day-old run was deleted in Health;
	# the 40-day-old one is outside the window, so it stays.
	out = await _sync(client, [kept], since=NOW - dt.timedelta(days=30))

	assert out["removed"] == 1
	assert await _health_rows(admin_engine) == 2


async def test_a_day_removed_in_memo_stays_removed_whatever_health_says(
	client: AsyncClient,
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	run = _workout(2)
	await _sync(client, [run])
	running = await _running(client)
	day = running["days"][0]["date"]
	removed = await client.delete(f"/v1/habits/{running['id']}/days/{day}")
	assert removed.status_code == 200 and removed.json()["total_days"] == 0

	await _sync(client, [run])
	await _sync(client, [])  # and a pass without it doesn't drop the removal either
	await _sync(client, [run])

	assert (await client.get(f"/v1/habits/{running['id']}")).json()["total_days"] == 0


async def test_one_workout_is_one_row_even_if_two_syncs_race(
	admin_engine: AsyncEngine,
) -> None:
	insert = text(
		"INSERT INTO derivations (id, user_id, kind, payload, status, "
		"extractor_version) VALUES (:id, :u, 'activity', CAST(:p AS jsonb), "
		"'accepted', 'health-v1')"
	)
	payload = '{"key": "running", "occurred_on": "2026-09-28", "health_id": "ABC"}'
	async with admin_engine.begin() as conn:
		await conn.execute(
			insert, {"id": uuid.uuid4(), "u": SEED_USER_ID, "p": payload}
		)
	with pytest.raises(IntegrityError, match="uq_derivations_health_workout"):
		async with admin_engine.begin() as conn:
			await conn.execute(
				insert, {"id": uuid.uuid4(), "u": SEED_USER_ID, "p": payload}
			)


async def test_health_runs_reach_milestones(client: AsyncClient) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	await _sync(
		client,
		[
			_workout(30, km=3),
			_workout(20, km=4.96),
			_workout(12, km=6),
			_workout(1, km=10.1, minutes=55),
		],
	)
	titles = {m["title"] for m in (await _running(client))["milestones"]}

	assert {"First 5k", "First 10k", "Longest run yet"} <= titles


async def test_turning_health_off_forgets_every_workout(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	await _sync(client, [_workout(1), _workout(2)])
	assert (await client.delete("/v1/workouts")).status_code == 204
	assert await _health_rows(admin_engine) == 0


@pytest.mark.parametrize(
	("change", "why"),
	[
		({"start": "2026-09-01T07:00:00"}, "timezone"),
		({"start": (NOW - dt.timedelta(days=90)).isoformat()}, "outside the window"),
		({"duration_s": -1}, "greater than or equal"),
	],
)
async def test_a_workout_that_cannot_be_right_is_refused(
	client: AsyncClient, change: dict[str, Any], why: str
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	r = await client.put(
		"/v1/workouts",
		json={
			"since": SINCE.isoformat(),
			"until": NOW.isoformat(),
			"workouts": [{**_workout(1), **change}],
		},
	)
	assert r.status_code == 422 and why in r.text


async def test_a_workout_listed_twice_is_refused(client: AsyncClient) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	run = _workout(1)
	r = await client.put(
		"/v1/workouts",
		json={
			"since": SINCE.isoformat(),
			"until": NOW.isoformat(),
			"workouts": [run, run],
		},
	)
	assert r.status_code == 422 and "listed twice" in r.text


async def test_workouts_are_the_users_own(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	shared_id = "SAME-UUID-ON-TWO-PHONES"
	await _sync(client, [_workout(1, id=shared_id)])
	as_user(second_user)
	await client.patch("/v1/me", json={"habits_enabled": True})
	# The same HealthKit id for someone else is theirs; an empty window of theirs
	# doesn't touch mine, and neither does turning theirs off.
	await _sync(client, [_workout(1, id=shared_id)])
	await _sync(client, [])
	await client.delete("/v1/workouts")

	assert await _health_rows(admin_engine) == 1
	assert await _health_rows(admin_engine, second_user) == 0
