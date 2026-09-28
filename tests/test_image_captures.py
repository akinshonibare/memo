"""Photo captures (ADR 0024): upload, describe, then the ordinary pipeline."""

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import EventPayload, Extraction
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import TerminalError
from tests.fakes import (
	FakeDescriber,
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakeTranscriber,
)


@pytest.fixture
def store(app: FastAPI) -> FakeObjectStore:
	fake = FakeObjectStore()
	app.state.object_store = fake
	return fake


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _photo(client: AsyncClient, store: FakeObjectStore, token: str) -> str:
	rec = await client.post(
		"/v1/recordings", json={"mime": "image/jpeg", "bytes": 1024}
	)
	assert rec.status_code == 201, rec.text
	key = rec.json()["upload_url"].split("fake-bucket.test/")[1].split("?")[0]
	assert key.endswith(".jpg")
	store.objects[key] = b"\xff\xd8jpeg-bytes"
	cap = await client.post(
		"/v1/captures",
		json={"recording_id": rec.json()["recording_id"], "client_token": token},
	)
	assert cap.status_code == 202, cap.text
	assert cap.json()["kind"] == "image"
	capture_id: str = cap.json()["id"]
	return capture_id


async def test_a_photo_is_described_then_extracted_like_any_note(
	client: AsyncClient,
	session: AsyncSession,
	store: FakeObjectStore,
	admin_engine: AsyncEngine,
) -> None:
	capture_id = await _photo(client, store, "tok-photo-1")
	describer = FakeDescriber()
	extractor = FakeExtractor(
		Extraction(
			events=[EventPayload(title="Jazz night", starts_at="2026-10-02T20:00:00Z")]
		)
	)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=store,
		extractor=extractor,
		embedder=FakeEmbedder(),
		describer=describer,
	)

	outcome = await process_capture(ctx, uuid.UUID(capture_id), SEED_USER_ID)

	assert outcome.status == "ready"
	assert describer.seen == [(len(b"\xff\xd8jpeg-bytes"), "image/jpeg")]
	assert extractor.seen[0][0].startswith("Photo: A flyer"), (
		"extraction reads the text"
	)
	detail = (await client.get(f"/v1/captures/{capture_id}")).json()
	assert detail["text"].startswith("Photo: A flyer for a jazz night.")
	assert [d["payload"]["title"] for d in detail["derivations"]] == ["Jazz night"]
	media = (await client.get(f"/v1/captures/{capture_id}/media")).json()
	assert media["mime"] == "image/jpeg"
	async with admin_engine.connect() as conn:
		stages: list[str] = list(
			(
				await conn.execute(
					text(
						"SELECT stage FROM job_runs WHERE capture_id = :c "
						"ORDER BY stage"
					),
					{"c": capture_id},
				)
			)
			.scalars()
			.all()
		)
		costed = await conn.scalar(
			text("SELECT count(*) FROM llm_calls WHERE stage = 'describe'")
		)
	assert list(stages) == ["describe", "embed", "extract"]
	assert costed == 1, "the vision call is priced and logged like every other"


async def test_a_photo_the_model_cannot_read_fails_visibly(
	client: AsyncClient, session: AsyncSession, store: FakeObjectStore
) -> None:
	capture_id = await _photo(client, store, "tok-photo-2")
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=store,
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		describer=FakeDescriber(fail_with=TerminalError("model declined")),
	)
	outcome = await process_capture(ctx, uuid.UUID(capture_id), SEED_USER_ID)
	assert outcome.status == "failed"
	detail = (await client.get(f"/v1/captures/{capture_id}")).json()
	assert detail["failure_reason"] == "describe: model declined"


async def test_unsupported_media_is_refused(client: AsyncClient) -> None:
	heic = await client.post("/v1/recordings", json={"mime": "image/heic"})
	assert heic.status_code == 422
