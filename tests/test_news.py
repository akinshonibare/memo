"""What's new in each place, for Home's Places button: the newest thing each holds."""

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import Extraction, TodoPayload
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	*,
	todos: list[str] = (),  # type: ignore[assignment]
	people: list[str] = (),  # type: ignore[assignment]
) -> None:
	made = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(
			Extraction(todos=[TodoPayload(title=t) for t in todos])
		),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(
			PeopleExtraction(people=[MentionPayload(name=n) for n in people])
		),
	)
	capture_id = uuid.UUID(made.json()["id"])
	assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"


async def test_nothing_is_new_in_an_empty_memo(client: AsyncClient) -> None:
	assert (await client.get("/v1/news")).json() == {
		"planner": None,
		"review": None,
		"people": None,
		"faith": None,
		"habits": None,
	}


async def test_a_todo_from_a_note_is_new_in_the_planner_and_review(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(
		client,
		session,
		"call Kemi about the flat",
		todos=["Call Kemi"],
		people=["Kemi"],
	)

	news = (await client.get("/v1/news")).json()

	assert news["planner"] and news["review"] and news["people"]
	assert news["faith"] is None and news["habits"] is None


async def test_faith_is_only_news_while_faith_is_on(client: AsyncClient) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	assert (
		await client.post("/v1/faith/prayers", json={"title": "Kemi's exams"})
	).status_code == 201
	assert (await client.get("/v1/news")).json()["faith"] is not None

	await client.patch("/v1/me", json={"faith_enabled": False})
	assert (await client.get("/v1/news")).json()["faith"] is None


async def test_a_day_counted_is_news_for_habits(client: AsyncClient) -> None:
	await client.patch("/v1/me", json={"habits_enabled": True})
	habit = (await client.post("/v1/habits", json={"name": "Gym"})).json()
	await client.post(f"/v1/habits/{habit['id']}/log", json={})

	assert (await client.get("/v1/news")).json()["habits"] is not None


async def test_news_is_the_users_own(
	client: AsyncClient,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await _note(client, session, "buy milk", todos=["Buy milk"])
	as_user(second_user)

	assert (await client.get("/v1/news")).json()["planner"] is None
