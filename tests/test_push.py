"""The morning push (ADR 0047): what it says, when it goes, once a day, only to a
signed-in phone, and what the phone says back. And the weekly push (ADR 0070): the
week in numbers, Sunday only, through the same ledger."""

import datetime as dt
import uuid
from collections.abc import Callable, Sequence

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID
from memo.services.morning import EventItem, MorningViewOut, TodoItem
from memo.services.push import (
	WeekFacts,
	check_receipts,
	clock,
	is_due,
	is_week_due,
	morning_message,
	send_due_mornings,
	send_due_weeks,
	week_message,
)
from memo.services.retry import RetryableError
from tests.conftest import issue_session
from tests.fakes import FakePushSender

TODAY = dt.date(2026, 9, 30)
PHONE = "ExponentPushToken[phone-one-0000001]"


def _view(
	events: Sequence[tuple[int, int, str, str | None]] = (),
	todos: Sequence[tuple[str, dt.date | None]] = (),
) -> MorningViewOut:
	return MorningViewOut(
		local_date=TODAY,
		timezone="Europe/London",
		todos=[
			TodoItem(id=uuid.uuid4(), capture_id=None, title=t, due=d, notes=None)
			for t, d in todos
		],
		events=[
			EventItem(
				id=uuid.uuid4(),
				capture_id=None,
				title=title,
				# 09:00 UTC is 10am in London in September.
				starts_at=dt.datetime(2026, 9, 30, h - 1, m, tzinfo=dt.UTC),
				ends_at=None,
				location=where,
			)
			for h, m, title, where in events
		],
		resurfaced_idea=None,
		built_at=dt.datetime(2026, 9, 30, 3, tzinfo=dt.UTC),
	)


def test_the_message_names_the_day_events_first() -> None:
	message = morning_message(
		_view(
			events=[(14, 30, "Call with Tunde", None), (10, 0, "Dentist", "Harley St")],
			todos=[("Renew passport", TODAY)],
		),
		TODAY,
	)
	assert message is not None
	assert message.title == "Today: 10am Dentist"
	assert message.body == "2:30pm Call with Tunde · Renew passport"


def test_one_thing_says_where_or_that_nothing_else_is_on() -> None:
	one_event = morning_message(_view(events=[(10, 0, "Dentist", "Harley St")]), TODAY)
	one_todo = morning_message(_view(todos=[("Renew passport", TODAY)]), TODAY)
	assert one_event is not None and one_event.body == "Harley St"
	assert one_todo is not None and one_todo.body == "Nothing else on."


def test_a_long_day_is_cut_short_with_a_count() -> None:
	todos = [(f"Todo {i}", TODAY) for i in range(6)]
	message = morning_message(_view(todos=todos), TODAY)
	assert message is not None
	assert message.body == "Todo 1 · Todo 2 · Todo 3 · 2 more"


def test_an_empty_day_and_yesterdays_todos_send_nothing() -> None:
	"""No guilt (brief): a slipped todo stays on Today in the app, never pushed."""
	assert morning_message(_view(), TODAY) is None
	stale = _view(todos=[("Call the bank", TODAY - dt.timedelta(days=3))])
	assert morning_message(stale, TODAY) is None


@pytest.mark.parametrize(
	("hour", "minute", "said"),
	[(0, 0, "12am"), (9, 5, "9:05am"), (12, 0, "12pm"), (14, 30, "2:30pm")],
)
def test_clock(hour: int, minute: int, said: str) -> None:
	assert clock(dt.datetime(2026, 9, 30, hour, minute)) == said


def test_due_from_the_time_until_two_hours_later_in_local_time() -> None:
	at = dt.time(7, 30)
	# 06:29 UTC is 07:29 in London (BST).
	assert not is_due(
		dt.datetime(2026, 9, 30, 6, 29, tzinfo=dt.UTC), "Europe/London", at
	)[0]
	assert is_due(
		dt.datetime(2026, 9, 30, 6, 30, tzinfo=dt.UTC), "Europe/London", at
	) == (
		True,
		TODAY,
	)
	assert not is_due(
		dt.datetime(2026, 9, 30, 8, 30, tzinfo=dt.UTC), "Europe/London", at
	)[0]
	# After the clocks go back, the same 07:30 is 07:30 UTC.
	after = dt.datetime(2026, 11, 2, 7, 31, tzinfo=dt.UTC)
	assert is_due(after, "Europe/London", at)[0]


# --- the week -----------------------------------------------------------------------


def test_the_week_message_is_the_week_in_numbers() -> None:
	full = week_message(WeekFacts(notes=14, done=6, highlights=3))
	assert full is not None
	assert (full.title, full.body) == (
		"Your week: 14 notes",
		"6 todos done · 3 highlights",
	)
	assert full.category == "week"
	one = week_message(WeekFacts(notes=1, done=1, highlights=1))
	assert one is not None
	assert (one.title, one.body) == ("Your week: 1 note", "1 todo done · 1 highlight")
	only_notes = week_message(WeekFacts(notes=2, done=0, highlights=0))
	assert only_notes is not None and only_notes.body == "Tap to look back."
	only_done = week_message(WeekFacts(notes=0, done=2, highlights=0))
	assert only_done is not None and only_done.title == "Your week"


def test_a_quiet_week_sends_nothing() -> None:
	assert week_message(WeekFacts(notes=0, done=0, highlights=0)) is None


def test_the_week_is_due_on_sunday_only() -> None:
	at = dt.time(18, 0)
	# 2026-10-04 is a Sunday; 17:00 UTC is 6pm in London (BST).
	sunday = dt.datetime(2026, 10, 4, 17, 1, tzinfo=dt.UTC)
	assert is_week_due(sunday, "Europe/London", at) == (True, dt.date(2026, 10, 4))
	saturday = sunday - dt.timedelta(days=1)
	assert is_week_due(saturday, "Europe/London", at) == (False, dt.date(2026, 10, 3))
	# Two hours past the time, the moment has gone, like the morning's.
	assert not is_week_due(sunday + dt.timedelta(hours=2), "Europe/London", at)[0]


# --- the whole path, against Postgres -------------------------------------------------


def _at(time: str) -> dt.datetime:
	"""Today at `time` UTC, the seed user's zone in tests."""
	h, m = (int(x) for x in time.split(":"))
	return dt.datetime.now(dt.UTC).replace(hour=h, minute=m, second=0, microsecond=0)


async def _day_with_an_event(client: AsyncClient) -> None:
	created = await client.post(
		"/v1/derivations",
		json={
			"kind": "event",
			"payload": {
				"title": "Dentist",
				"starts_at": _at("10:00").isoformat(),
				"location": "Harley St",
			},
		},
	)
	assert created.status_code == 201, created.text


async def _pushes(engine: AsyncEngine) -> list[dict[str, object]]:
	async with engine.connect() as conn:
		rows = await conn.execute(
			text(
				"SELECT id, status, reason, title, body, tickets, opened_at, feedback "
				"FROM pushes ORDER BY local_date"
			)
		)
		return [dict(r._mapping) for r in rows]


async def _set_up(client: AsyncClient, token: str = PHONE) -> None:
	me = await client.patch("/v1/me", json={"morning_push_at": "07:30"})
	assert me.status_code == 200 and me.json()["morning_push_at"] == "07:30:00"
	put = await client.put("/v1/push/token", json={"token": token})
	assert put.status_code == 204, put.text


async def test_the_morning_push_goes_once_with_the_day_in_it(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up(client)
	await _day_with_an_event(client)
	sender = FakePushSender()
	factory = app.state.session_factory

	assert await send_due_mornings(factory, sender, now=_at("07:29")) == {}
	assert await send_due_mornings(factory, sender, now=_at("07:31")) == {"sent": 1}
	assert await send_due_mornings(factory, sender, now=_at("07:36")) == {"already": 1}

	assert len(sender.sent) == 1, "at most once a day, however often the sweep runs"
	to, title, body, data = sender.sent[0]
	assert to == [PHONE]
	assert (title, body) == ("Today: 10am Dentist", "Harley St")
	(push,) = await _pushes(admin_engine)
	assert push["status"] == "sent" and data == {
		"kind": "morning",
		"push_id": str(push["id"]),
	}


async def test_an_empty_day_is_recorded_as_skipped_and_sends_nothing(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up(client)
	sender = FakePushSender()

	outcome = await send_due_mornings(
		app.state.session_factory, sender, now=_at("07:31")
	)

	assert outcome == {"skipped": 1} and sender.sent == []
	(push,) = await _pushes(admin_engine)
	assert (push["status"], push["reason"]) == ("skipped", "nothing today")


async def test_a_signed_out_phone_gets_nothing(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up(client)
	await _day_with_an_event(client)
	assert (await client.post("/v1/auth/logout")).status_code == 204
	sender = FakePushSender()

	await send_due_mornings(app.state.session_factory, sender, now=_at("07:31"))

	assert sender.sent == []
	(push,) = await _pushes(admin_engine)
	assert (push["status"], push["reason"]) == ("skipped", "no phone")


async def test_turning_it_off_stops_it(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up(client)
	await _day_with_an_event(client)
	await client.patch("/v1/me", json={"morning_push_at": None})
	sender = FakePushSender()

	assert (
		await send_due_mornings(app.state.session_factory, sender, now=_at("07:31"))
		== {}
	)
	assert await _pushes(admin_engine) == []


async def test_a_provider_failure_is_recorded_not_retried(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up(client)
	await _day_with_an_event(client)
	sender = FakePushSender(fail_with=RetryableError("expo push unreachable"))
	factory = app.state.session_factory

	assert await send_due_mornings(factory, sender, now=_at("07:31")) == {"failed": 1}
	assert await send_due_mornings(factory, sender, now=_at("07:36")) == {"already": 1}
	(push,) = await _pushes(admin_engine)
	assert push["status"] == "failed" and "unreachable" in str(push["reason"])


async def test_a_phone_apple_says_is_gone_is_dropped(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	"""By the ticket at once, or by the receipt fifteen minutes later."""
	await _set_up(client)
	second = "ExponentPushToken[phone-two-0000002]"
	assert (
		await client.put("/v1/push/token", json={"token": second})
	).status_code == 204
	await _day_with_an_event(client)
	factory = app.state.session_factory
	sender = FakePushSender(
		errors={second: "DeviceNotRegistered"},
		receipts={f"ticket-{PHONE}": "DeviceNotRegistered"},
	)

	assert await send_due_mornings(factory, sender, now=_at("07:31")) == {"sent": 1}
	async with admin_engine.connect() as conn:
		disabled: set[str] = set(
			(
				await conn.execute(
					text("SELECT token FROM push_tokens WHERE disabled_at IS NOT NULL")
				)
			).scalars()
		)
	assert disabled == {second}

	later = dt.datetime.now(dt.UTC) + dt.timedelta(minutes=16)
	assert await check_receipts(factory, sender, now=later) == 1
	assert await check_receipts(factory, sender, now=later) == 0, "once per push"
	async with admin_engine.connect() as conn:
		live = (
			await conn.execute(
				text("SELECT count(*) FROM push_tokens WHERE disabled_at IS NULL")
			)
		).scalar()
	assert live == 0
	# The phone comes back (the app reinstalled): registering again re-enables it.
	assert (
		await client.put("/v1/push/token", json={"token": PHONE})
	).status_code == 204
	async with admin_engine.connect() as conn:
		live = (
			await conn.execute(
				text("SELECT count(*) FROM push_tokens WHERE disabled_at IS NULL")
			)
		).scalar()
	assert live == 1


async def test_opened_and_feedback_make_the_act_on_rate(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up(client)
	await _day_with_an_event(client)
	await send_due_mornings(
		app.state.session_factory, FakePushSender(), now=_at("07:31")
	)
	(push,) = await _pushes(admin_engine)
	push_id = push["id"]

	assert (await client.post(f"/v1/pushes/{push_id}/opened")).status_code == 204
	voted = await client.post(f"/v1/pushes/{push_id}/feedback", json={"vote": "down"})
	assert voted.status_code == 204
	voted = await client.post(f"/v1/pushes/{push_id}/feedback", json={"vote": "up"})
	stats = (await client.get("/v1/pushes")).json()

	assert (stats["sent"], stats["opened"], stats["act_on_rate"]) == (1, 1, 1.0)
	assert (stats["up"], stats["down"]) == (1, 0), "the latest vote stands"
	assert stats["pushes"][0]["title"] == "Today: 10am Dentist"


async def test_another_account_can_neither_see_nor_vote_on_a_push(
	client: AsyncClient,
	app: FastAPI,
	admin_engine: AsyncEngine,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await _set_up(client)
	await _day_with_an_event(client)
	await send_due_mornings(
		app.state.session_factory, FakePushSender(), now=_at("07:31")
	)
	(push,) = await _pushes(admin_engine)

	as_user(second_user)

	assert (await client.post(f"/v1/pushes/{push['id']}/opened")).status_code == 404
	assert (await client.get("/v1/pushes")).json()["pushes"] == []


async def test_a_phone_signed_into_another_account_moves_to_it(
	client: AsyncClient,
	app: FastAPI,
	admin_engine: AsyncEngine,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await _set_up(client)
	as_user(second_user)
	# The other account signs in on the same phone: its own session, its own token.
	theirs = await issue_session(app, second_user)

	moved = await client.put(
		"/v1/push/token",
		json={"token": PHONE},
		headers={"Authorization": f"Bearer {theirs}"},
	)
	stolen = await client.put("/v1/push/token", json={"token": PHONE})

	assert moved.status_code == 204, moved.text
	async with admin_engine.connect() as conn:
		owners: Sequence[uuid.UUID] = (
			(
				await conn.execute(
					text("SELECT user_id FROM push_tokens WHERE token = :t"),
					{"t": PHONE},
				)
			)
			.scalars()
			.all()
		)
	assert owners == [second_user], "one account at a time, never both"
	assert SEED_USER_ID not in owners
	# A token presented for a user it doesn't belong to registers nothing.
	assert stolen.status_code == 401


@pytest.mark.parametrize(
	"token", ["", "hello", "ExponentPushToken[]", "ExponentPushToken[a b c d e f g h]"]
)
async def test_only_expo_tokens_are_accepted(client: AsyncClient, token: str) -> None:
	assert (
		await client.put("/v1/push/token", json={"token": token})
	).status_code == 422


# --- the weekly push, against Postgres ----------------------------------------------


def _this_sunday_at(time: str) -> dt.datetime:
	"""This week's Sunday at `time` UTC (the seed user's zone in tests), so a note
	kept now falls inside the week the push counts."""
	today = dt.datetime.now(dt.UTC).date()
	sunday = today + dt.timedelta(days=(6 - today.weekday()) % 7)
	h, m = (int(x) for x in time.split(":"))
	return dt.datetime.combine(sunday, dt.time(h, m), dt.UTC)


async def _set_up_week(client: AsyncClient) -> None:
	me = await client.patch("/v1/me", json={"weekly_push_at": "18:00"})
	assert me.status_code == 200 and me.json()["weekly_push_at"] == "18:00:00"
	assert (
		await client.put("/v1/push/token", json={"token": PHONE})
	).status_code == 204


async def test_the_weekly_push_goes_once_on_sunday_with_the_week_in_it(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up_week(client)
	kept = await client.post(
		"/v1/captures", json={"text": "x", "client_token": "wk-note-0001"}
	)
	assert kept.status_code == 202, kept.text
	done = (
		await client.post(
			"/v1/derivations", json={"kind": "todo", "payload": {"title": "Pay rent"}}
		)
	).json()
	await client.patch(f"/v1/derivations/{done['id']}", json={"completed": True})
	idea = await client.post(
		"/v1/derivations",
		json={"kind": "idea", "payload": {"title": "Seat pricing", "summary": "s"}},
	)
	assert idea.status_code == 201, idea.text
	sender = FakePushSender()
	factory = app.state.session_factory

	saturday = _this_sunday_at("18:01") - dt.timedelta(days=1)
	assert await send_due_weeks(factory, sender, now=saturday) == {}
	assert await send_due_weeks(factory, sender, now=_this_sunday_at("17:59")) == {}
	assert await send_due_weeks(factory, sender, now=_this_sunday_at("18:01")) == {
		"sent": 1
	}
	assert await send_due_weeks(factory, sender, now=_this_sunday_at("18:06")) == {
		"already": 1
	}

	assert len(sender.sent) == 1, "once a week, however often the sweep runs"
	to, title, body, data = sender.sent[0]
	assert to == [PHONE]
	assert (title, body) == ("Your week: 1 note", "1 todo done · 1 highlight")
	(push,) = await _pushes(admin_engine)
	assert push["status"] == "sent"
	assert data == {"kind": "week", "push_id": str(push["id"])}


async def test_a_quiet_week_is_recorded_as_skipped(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _set_up_week(client)
	sender = FakePushSender()

	outcome = await send_due_weeks(
		app.state.session_factory, sender, now=_this_sunday_at("18:01")
	)

	assert outcome == {"skipped": 1} and sender.sent == []
	(push,) = await _pushes(admin_engine)
	assert (push["status"], push["reason"]) == ("skipped", "nothing this week")


async def test_the_morning_and_the_week_are_separate_claims(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	"""One of each kind a day at most: a Sunday with a morning summary still gets
	its week, and neither blocks the other."""
	await _set_up(client)
	await _set_up_week(client)
	await _day_with_an_event(client)
	kept = await client.post(
		"/v1/captures", json={"text": "x", "client_token": "wk-note-0002"}
	)
	assert kept.status_code == 202, kept.text
	factory = app.state.session_factory
	sender = FakePushSender()

	# The event sits on today's date, which is Sunday only on a Sunday: the morning
	# row is claimed either way (sent, or skipped as "nothing today").
	morning = await send_due_mornings(factory, sender, now=_this_sunday_at("07:31"))
	week = await send_due_weeks(factory, sender, now=_this_sunday_at("18:01"))

	assert sum(morning.values()) == 1 and week == {"sent": 1}
	async with admin_engine.connect() as conn:
		result = await conn.execute(text("SELECT kind FROM pushes ORDER BY kind"))
		kinds: list[str] = [str(row[0]) for row in result.all()]
	assert kinds == ["morning", "week"]
