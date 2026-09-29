"""Deleting a note deletes everything taken from it (ADR 0053, superseding 0022), and
items detached by deletes from before that keep working everywhere (#55, #56)."""

import datetime as dt
import json
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import Extraction, IdeaPayload, TodoPayload
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeAnswerProvider,
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


async def _processed(
	client: AsyncClient, session: AsyncSession, body: str, token: str
) -> tuple[str, dict[str, str]]:
	"""A processed note with a todo, an idea and a third item the user then rejects."""
	extraction = Extraction(
		todos=[
			TodoPayload(title="Call Tunde about pricing", due=dt.date(2026, 10, 1)),
			TodoPayload(title="Misread item"),
		],
		ideas=[IdeaPayload(title="Price by seat", summary="Seats, not usage.")],
	)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)
	created = await client.post(
		"/v1/captures", json={"text": body, "client_token": token}
	)
	assert created.status_code == 202, created.text
	capture_id = created.json()["id"]
	assert (
		await process_capture(ctx, uuid.UUID(capture_id), SEED_USER_ID)
	).status == "ready"
	detail = (await client.get(f"/v1/captures/{capture_id}")).json()
	ids = {d["payload"]["title"]: d["id"] for d in detail["derivations"]}
	await client.patch(
		f"/v1/derivations/{ids['Call Tunde about pricing']}",
		json={"status": "confirmed"},
	)
	await client.patch(
		f"/v1/derivations/{ids['Misread item']}", json={"status": "rejected"}
	)
	return capture_id, ids


async def _ask(client: AsyncClient, question: str) -> None:
	conversation = (await client.post("/v1/conversations")).json()["id"]
	asked = await client.post(
		f"/v1/conversations/{conversation}/messages", json={"text": question}
	)
	async with client.stream("GET", asked.json()["stream_url"]) as response:
		await response.aread()


async def test_deleting_a_note_deletes_everything_taken_from_it(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	capture_id, ids = await _processed(
		client,
		session,
		"need to call Tunde about pricing, and price by seat",
		"tok-del-1",
	)
	other, _ = await _processed(client, session, "call Tunde", "tok-del-2")
	# An answer cites the note's chunks: this delete used to fail on the FK (#55).
	await _ask(client, "what about Tunde and pricing?")

	deleted = await client.delete(f"/v1/captures/{capture_id}")
	assert deleted.status_code == 204, deleted.text

	assert (await client.get(f"/v1/captures/{capture_id}")).status_code == 404
	left = (await client.get("/v1/derivations")).json()["items"]
	assert left and {d["capture_id"] for d in left} == {other}, "the other note's stay"
	assert not {d["id"] for d in left} & set(ids.values())
	async with admin_engine.connect() as conn:
		items = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE id = ANY(:ids)"),
			{"ids": [uuid.UUID(i) for i in ids.values()]},
		)
		chunks = await conn.scalar(
			text(
				"SELECT count(*) FROM chunks WHERE capture_id = :c "
				"OR derivation_id = ANY(:ids)"
			),
			{"c": capture_id, "ids": [uuid.UUID(i) for i in ids.values()]},
		)
		messages = await conn.scalar(text("SELECT count(*) FROM messages"))
	assert items == 0, "rejected, pending and confirmed alike"
	assert chunks == 0, "nothing of the deleted note is left to answer questions"
	assert messages == 2, "the conversation stays; only its citations of the note go"


async def test_ask_no_longer_sees_a_deleted_notes_items(
	app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
	capture_id, _ = await _processed(
		client, session, "need to call Tunde about pricing", "tok-del-3"
	)
	await client.delete(f"/v1/captures/{capture_id}")

	await _ask(client, "Tunde pricing")

	provider: FakeAnswerProvider = app.state.answer_provider
	assert "Call Tunde about pricing" not in str(provider.last_messages[-1]["content"])


async def test_items_detached_before_0053_still_answer_and_export(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	delete_keeping_items: Callable[..., Any],
) -> None:
	capture_id, ids = await _processed(
		client, session, "need to call Tunde about pricing", "tok-del-4"
	)
	await delete_keeping_items(capture_id)

	kept = (await client.get("/v1/derivations")).json()["items"]
	assert {d["id"]: d["capture_id"] for d in kept} == {
		ids["Call Tunde about pricing"]: None,
		ids["Price by seat"]: None,
	}
	await _ask(client, "Tunde pricing")
	provider: FakeAnswerProvider = app.state.answer_provider
	prompt = str(provider.last_messages[-1]["content"])
	assert "(the note this came from was deleted)" in prompt
	assert "todo: Call Tunde about pricing · due Thu 1 Oct 2026 (confirmed)" in prompt
	export = (await client.get("/v1/me/export")).json()
	assert {d["id"] for d in export["derivations_without_note"]} == set(
		ids[t] for t in ("Call Tunde about pricing", "Price by seat")
	)


async def test_a_text_chunk_cannot_outlive_its_note_on_any_delete_path(
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
	second_user: uuid.UUID,
) -> None:
	# Bypassing the route entirely: the trigger removes the text chunks, and the items
	# go too, because the database cascades them (migration 0022), chunks and all.
	capture_id, _ = await _processed(client, session, "call Tunde", "tok-del-5")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("DELETE FROM captures WHERE id = :id"), {"id": capture_id}
		)
		chunks = await conn.scalar(text("SELECT count(*) FROM chunks"))
		items = await conn.scalar(text("SELECT count(*) FROM derivations"))
	assert (chunks, items) == (0, 0)

	# Deleting a whole user cascades to captures and chunks at once; the order of those
	# two foreign-key actions is Postgres's, and it must not matter (CI found this).
	async with admin_engine.begin() as conn:
		other = uuid.uuid4()
		await conn.execute(
			text(
				"INSERT INTO captures (id, user_id, kind, text, client_token) "
				"VALUES (:id, :uid, 'text', 'theirs', 'tok-other-user')"
			),
			{"id": other, "uid": second_user},
		)
		await conn.execute(
			text(
				"INSERT INTO chunks (id, user_id, capture_id, ordinal, text, "
				"char_start, char_end, embedding, embedding_model, embedding_dim) "
				"SELECT gen_random_uuid(), :uid, :cid, 0, 'theirs', 0, 6, embedding, "
				"embedding_model, embedding_dim FROM chunks LIMIT 1"
			),
			{"uid": second_user, "cid": other},
		)
		await conn.execute(
			text("DELETE FROM users WHERE id = :id"), {"id": second_user}
		)
		left = await conn.scalar(
			text("SELECT count(*) FROM chunks WHERE user_id = :u"), {"u": second_user}
		)
	assert left == 0


async def test_delete_everything_takes_detached_items_too(
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
	delete_keeping_items: Callable[..., Any],
) -> None:
	capture_id, _ = await _processed(client, session, "call Tunde", "tok-del-6")
	await delete_keeping_items(capture_id)

	deleted = await client.delete("/v1/me/data", params={"confirm": str(SEED_USER_ID)})
	assert deleted.status_code == 200, deleted.text
	async with admin_engine.connect() as conn:
		left = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE user_id = :u"),
			{"u": SEED_USER_ID},
		)
	assert left == 0
	assert json.loads(deleted.text)["deleted"]["derivations"] == 2
