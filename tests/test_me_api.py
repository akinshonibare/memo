"""Export round-trips; deletion leaves nothing retrievable (issue #49)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retrieval import search
from tests.fakes import FakeEmbedder, FakeExtractor, FakeObjectStore, FakeTranscriber


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


@pytest.fixture
def store(app: FastAPI) -> FakeObjectStore:
	fake = FakeObjectStore()
	app.state.object_store = fake
	return fake


async def _populate(
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
	store: FakeObjectStore,
) -> dict[str, str]:
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber("spoken note about Tunde"),
		object_store=store,
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
	)
	text_capture = (
		await client.post(
			"/v1/captures",
			json={"text": "typed note about Tunde", "client_token": "tok-me-1"},
		)
	).json()["id"]
	rec = (await client.post("/v1/recordings", json={"mime": "audio/m4a"})).json()
	async with admin_engine.connect() as conn:
		key = await conn.scalar(
			text("SELECT storage_key FROM recordings WHERE id = :id"),
			{"id": rec["recording_id"]},
		)
	assert key is not None
	store.objects[key] = b"audio"
	audio_capture = (
		await client.post(
			"/v1/captures",
			json={"recording_id": rec["recording_id"], "client_token": "tok-me-2"},
		)
	).json()["id"]
	for cid in (text_capture, audio_capture):
		assert (
			await process_capture(ctx, uuid.UUID(cid), SEED_USER_ID)
		).status == "ready"
	await client.post(
		f"/v1/captures/{text_capture}/derivations",
		json={"kind": "todo", "payload": {"title": "Call Tunde"}},
	)
	conversation = (await client.post("/v1/conversations")).json()["id"]
	asked = await client.post(
		f"/v1/conversations/{conversation}/messages",
		json={"text": "what about Tunde?"},
	)
	# Stream the answer so citations exist: they point at the captures' chunks, and a
	# delete that removes captures before conversations trips on them (#55).
	async with client.stream("GET", asked.json()["stream_url"]) as response:
		await response.aread()
	return {"text": text_capture, "audio": audio_capture, "key": str(key)}


async def test_export_round_trips_everything(
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
	store: FakeObjectStore,
) -> None:
	ids = await _populate(client, session, admin_engine, store)

	export = (await client.get("/v1/me/export")).json()

	assert export["user_id"] == str(SEED_USER_ID)
	assert {c["id"] for c in export["captures"]} == {ids["text"], ids["audio"]}
	audio = next(c for c in export["captures"] if c["id"] == ids["audio"])
	assert audio["text"] == "spoken note about Tunde", "the transcript is exported"
	typed = next(c for c in export["captures"] if c["id"] == ids["text"])
	assert [d["payload"]["title"] for d in typed["derivations"]] == ["Call Tunde"]
	assert export["recordings"][0]["storage_key"] == ids["key"]
	assert export["recordings"][0]["download_url"].startswith(
		"https://fake-bucket.test/"
	)
	assert [m["role"] for m in export["conversations"][0]["messages"]] == [
		"user",
		"assistant",
	]


async def test_delete_leaves_nothing_retrievable_and_spares_other_users(
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
	store: FakeObjectStore,
	app: FastAPI,
	second_user: uuid.UUID,
) -> None:
	ids = await _populate(client, session, admin_engine, store)
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO captures (id, user_id, kind, text, client_token) "
				"VALUES (:id, :uid, 'text', 'theirs', 'tok-other')"
			),
			{"id": uuid.uuid4(), "uid": second_user},
		)

	refused = await client.delete("/v1/me/data")
	assert refused.status_code == 400
	deleted = await client.delete("/v1/me/data", params={"confirm": str(SEED_USER_ID)})
	assert deleted.status_code == 200, deleted.text
	assert deleted.json()["deleted"]["captures"] == 2
	assert deleted.json()["deleted"]["recording_objects"] == 1

	assert store.deleted == [ids["key"]]
	async with admin_engine.connect() as conn:
		for table in (
			"captures",
			"derivations",
			"chunks",
			"recordings",
			"job_runs",
			"conversations",
			"messages",
			"message_citations",
			"llm_calls",
		):
			mine = await conn.scalar(
				text(f"SELECT count(*) FROM {table} WHERE user_id = :uid"),
				{"uid": SEED_USER_ID},
			)
			assert mine == 0, table
		theirs = await conn.scalar(
			text("SELECT count(*) FROM captures WHERE user_id = :uid"),
			{"uid": second_user},
		)
		assert theirs == 1
		account = await conn.scalar(
			text("SELECT count(*) FROM users WHERE id = :uid"), {"uid": SEED_USER_ID}
		)
		assert account == 1, "the account row stays; only data is removed"

	async with app.state.session_factory() as fresh:
		await bind_user(fresh, SEED_USER_ID)
		result = await search(
			fresh, user_id=SEED_USER_ID, query="Tunde", embedder=FakeEmbedder()
		)
	assert result.hits == [] and result.vector_ranked == []
	assert (await client.get("/v1/captures")).json()["items"] == []


async def test_timezone_is_readable_and_settable(client: AsyncClient) -> None:
	assert (await client.get("/v1/me")).json()["timezone"] == "UTC"
	updated = await client.patch("/v1/me", json={"timezone": "America/Toronto"})
	assert updated.status_code == 200, updated.text
	assert updated.json()["timezone"] == "America/Toronto"
	assert (await client.get("/v1/me")).json()["timezone"] == "America/Toronto"


async def test_unknown_timezone_is_a_422(client: AsyncClient) -> None:
	assert (
		await client.patch("/v1/me", json={"timezone": "Mars/Olympus_Mons"})
	).status_code == 422
	assert (await client.get("/v1/me")).json()["timezone"] == "UTC"
