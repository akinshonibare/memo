"""Planner support: ticking a todo off, and items added with no note behind them."""

import datetime as dt
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.services.morning import build_morning_view
from tests.fakes import FakeQueue


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def test_a_standalone_item_has_no_note_and_is_indexed(
	client: AsyncClient, queue: FakeQueue
) -> None:
	created = await client.post(
		"/v1/derivations",
		json={
			"kind": "todo",
			"payload": {"title": "Book the MOT", "due": "2026-10-01"},
		},
	)
	assert created.status_code == 201, created.text
	body = created.json()
	assert body["capture_id"] is None
	assert (body["status"], body["extractor_version"]) == ("user_created", "user")
	assert [str(d) for d, _, _ in queue.embeds] == [body["id"]]

	bad = await client.post(
		"/v1/derivations", json={"kind": "event", "payload": {"title": "no time"}}
	)
	assert bad.status_code == 422


async def test_ticking_a_todo_off_and_back_on(
	client: AsyncClient, session: AsyncSession
) -> None:
	today = dt.datetime.now(dt.UTC).date().isoformat()
	todo = (
		await client.post(
			"/v1/derivations",
			json={"kind": "todo", "payload": {"title": "Call Tunde", "due": today}},
		)
	).json()

	done = await client.patch(f"/v1/derivations/{todo['id']}", json={"completed": True})
	assert done.status_code == 200, done.text
	assert done.json()["completed_at"] is not None
	assert done.json()["status"] == "user_created", "a tick is not a review"
	view = await build_morning_view(session, SEED_USER_ID)
	assert view.todos == [], "done todos leave Today"

	undone = await client.patch(
		f"/v1/derivations/{todo['id']}", json={"completed": False}
	)
	assert undone.json()["completed_at"] is None


async def test_only_a_todo_can_be_completed(client: AsyncClient) -> None:
	idea = (
		await client.post(
			"/v1/derivations",
			json={"kind": "idea", "payload": {"title": "x", "summary": "y"}},
		)
	).json()
	refused = await client.patch(
		f"/v1/derivations/{idea['id']}", json={"completed": True}
	)
	assert refused.status_code == 422
