"""The profile: a name the assistant uses, a voice, sign out everywhere, and deleting
the account (App Store guideline 5.1.1(v))."""

import uuid
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import Extraction, TodoPayload
from memo.services.pipeline import PipelineContext, process_capture
from tests.conftest import issue_session
from tests.fakes import (
	FakeAnswerProvider,
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeSpeaker,
	FakeTranscriber,
)


def _as(app: FastAPI, token: str) -> AsyncClient:
	return AsyncClient(
		transport=ASGITransport(app=app),
		base_url="http://test",
		headers={"Authorization": f"Bearer {token}"},
	)


async def test_name_and_voice_are_set_cleaned_and_cleared(client: AsyncClient) -> None:
	me = (await client.get("/v1/me")).json()
	assert (me["name"], me["voice"]) == (None, None)

	named = await client.patch("/v1/me", json={"name": "  Akin \n  Shonibare​ "})
	assert named.status_code == 200, named.text
	assert named.json()["name"] == "Akin Shonibare", "one line, no control characters"

	voiced = await client.patch("/v1/me", json={"voice": "aura-2-draco-en"})
	assert voiced.json()["voice"] == "aura-2-draco-en"
	assert voiced.json()["name"] == "Akin Shonibare", "a patch changes only its fields"

	assert (await client.patch("/v1/me", json={"voice": "robot"})).status_code == 422
	assert (await client.patch("/v1/me", json={})).status_code == 422
	cleared = await client.patch("/v1/me", json={"name": ""})
	assert cleared.json()["name"] is None

	voices = (await client.get("/v1/me/voices")).json()
	assert {"id": "aura-2-draco-en", "label": "Draco · warm baritone (UK)"} in voices


async def test_ask_uses_the_name_and_speech_uses_the_voice(
	app: FastAPI, client: AsyncClient
) -> None:
	await client.patch("/v1/me", json={"name": "Akin", "voice": "aura-2-helena-en"})
	conversation = (await client.post("/v1/conversations")).json()["id"]
	asked = await client.post(
		f"/v1/conversations/{conversation}/messages", json={"text": "anything today?"}
	)
	async with client.stream("GET", asked.json()["stream_url"]) as response:
		await response.aread()
	provider: FakeAnswerProvider = app.state.answer_provider
	assert str(provider.last_messages[-1]["content"]).startswith(
		"You are talking with Akin."
	)

	await client.post("/v1/voice/speak", json={"text": "Hello there."})
	await client.post(
		"/v1/voice/speak", json={"text": "Preview.", "voice": "aura-2-apollo-en"}
	)
	speaker: FakeSpeaker = app.state.speaker
	assert speaker.voices == ["aura-2-helena-en", "aura-2-apollo-en"]


async def test_apple_full_name_seeds_the_name_once(app: FastAPI) -> None:
	async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as a:
		first = await a.post(
			"/v1/auth/apple",
			json={"identity_token": "apple:sub-n", "full_name": "Akin  Shonibare"},
		)
		assert first.json()["user"]["name"] == "Akin Shonibare"
		async with _as(app, first.json()["token"]) as me:
			await me.patch("/v1/me", json={"name": "Akin"})
		again = await a.post(
			"/v1/auth/apple",
			json={"identity_token": "apple:sub-n", "full_name": "Someone Else"},
		)
		assert again.json()["user"]["name"] == "Akin", (
			"a chosen name is never overwritten"
		)


async def test_sign_out_everywhere_revokes_every_session(app: FastAPI) -> None:
	phone, laptop = (
		await issue_session(app, SEED_USER_ID),
		await issue_session(app, SEED_USER_ID),
	)
	async with _as(app, phone) as a, _as(app, laptop) as b:
		assert (await b.get("/v1/me")).status_code == 200
		assert (await a.post("/v1/auth/logout-all")).status_code == 204
		assert (await a.get("/v1/me")).status_code == 401
		assert (await b.get("/v1/me")).status_code == 401


async def test_deleting_the_account_leaves_no_row_anywhere(
	app: FastAPI,
	client: AsyncClient,
	admin_engine: AsyncEngine,
	second_user: uuid.UUID,
	delete_keeping_items: Callable[..., Any],
) -> None:
	# The account being deleted is second_user's; the seeded user (everyone else's
	# fixture) must come through untouched.
	await client.post(
		"/v1/derivations", json={"kind": "todo", "payload": {"title": "Seed's"}}
	)
	token = await issue_session(app, second_user)
	async with (
		_as(app, token) as them,
		app.state.session_factory() as session,
	):
		await bind_user(session, second_user)
		ctx = PipelineContext(
			session=session,
			transcriber=FakeTranscriber(),
			object_store=FakeObjectStore(),
			extractor=FakeExtractor(
				Extraction(todos=[TodoPayload(title="Call Tunde")])
			),
			embedder=FakeEmbedder(),
			people=FakePeopleReader(),
		)
		created = await them.post(
			"/v1/captures", json={"text": "call Tunde", "client_token": "tok-account-1"}
		)
		capture_id = uuid.UUID(created.json()["id"])
		await process_capture(ctx, capture_id, second_user)
		await delete_keeping_items(capture_id)  # a detached item, as before ADR 0053
		await them.post(
			"/v1/derivations", json={"kind": "todo", "payload": {"title": "MOT"}}
		)
		conversation = (await them.post("/v1/conversations")).json()["id"]
		asked = await them.post(
			f"/v1/conversations/{conversation}/messages", json={"text": "Tunde?"}
		)
		async with them.stream("GET", asked.json()["stream_url"]) as response:
			await response.aread()

		assert (await them.delete("/v1/me")).status_code == 400
		deleted = await them.delete("/v1/me", params={"confirm": str(second_user)})
		assert deleted.status_code == 204, deleted.text
		assert (await them.get("/v1/me")).status_code == 401, "sessions died with it"

	# Every table that holds user data, found rather than listed: a table added later
	# cannot quietly survive account deletion.
	async with admin_engine.connect() as conn:
		tables: list[str] = list(
			(
				await conn.execute(
					text(
						"SELECT table_name FROM information_schema.columns "
						"WHERE table_schema = 'public' AND column_name = 'user_id'"
					)
				)
			)
			.scalars()
			.all()
		)
		assert "derivations" in tables and "sessions" in tables
		for table in tables:
			left = await conn.scalar(
				text(f"SELECT count(*) FROM {table} WHERE user_id = :u"),
				{"u": second_user},
			)
			assert left == 0, table
		users = await conn.scalar(
			text("SELECT count(*) FROM users WHERE id = :u"), {"u": second_user}
		)
		seeds = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE user_id = :u"),
			{"u": SEED_USER_ID},
		)
	assert (users, seeds) == (0, 1)
