"""process_capture called directly - no arq, no Redis (issue #12's done-when)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import RetryableError, TerminalError
from tests.fakes import FakeObjectStore, FakeTranscriber


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		yield s


@pytest.fixture
def store(app: FastAPI) -> FakeObjectStore:
	# The API's "has it been uploaded?" check must see the same store the pipeline
	# reads from, so both point at the fake here.
	fake = FakeObjectStore()
	app.state.object_store = fake
	return fake


async def _audio_capture(
	client: AsyncClient, admin_engine: AsyncEngine, store: FakeObjectStore, token: str
) -> uuid.UUID:
	rec = (await client.post("/v1/recordings", json={"mime": "audio/m4a"})).json()
	async with admin_engine.connect() as conn:
		key = await conn.scalar(
			text("SELECT storage_key FROM recordings WHERE id = :id"),
			{"id": rec["recording_id"]},
		)
	assert key is not None
	store.objects[key] = b"fake-audio"
	cap = await client.post(
		"/v1/captures",
		json={"recording_id": rec["recording_id"], "client_token": token},
	)
	assert cap.status_code == 202, cap.text
	return uuid.UUID(cap.json()["id"])


async def _capture_row(
	admin_engine: AsyncEngine, capture_id: uuid.UUID
) -> dict[str, object]:
	async with admin_engine.connect() as conn:
		row = (
			(
				await conn.execute(
					text(
						"SELECT status, text, failure_reason, attempt_count "
						"FROM captures WHERE id = :id"
					),
					{"id": capture_id},
				)
			)
			.mappings()
			.one()
		)
	return dict(row)


async def _runs(
	admin_engine: AsyncEngine, capture_id: uuid.UUID
) -> list[tuple[str, str, int]]:
	async with admin_engine.connect() as conn:
		rows = (
			await conn.execute(
				text(
					"SELECT stage, status, attempt FROM job_runs "
					"WHERE capture_id = :id ORDER BY started_at"
				),
				{"id": capture_id},
			)
		).all()
	return [(r[0], r[1], r[2]) for r in rows]


async def test_audio_capture_becomes_text_and_ready(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-1")
	transcriber = FakeTranscriber("remember to book the dentist")
	ctx = PipelineContext(session=session, transcriber=transcriber, object_store=store)

	outcome = await process_capture(ctx, capture_id, SEED_USER_ID)

	assert outcome.status == "ready" and outcome.retry_after is None
	row = await _capture_row(admin_engine, capture_id)
	assert row["status"] == "ready"
	assert row["text"] == "remember to book the dentist"
	assert transcriber.calls == 1
	assert await _runs(admin_engine, capture_id) == [
		("transcribe", "succeeded", 1),
		("extract", "succeeded", 1),
		("embed", "succeeded", 1),
	]


async def test_running_twice_transcribes_once(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-2")
	transcriber = FakeTranscriber()
	ctx = PipelineContext(session=session, transcriber=transcriber, object_store=store)

	await process_capture(ctx, capture_id, SEED_USER_ID)
	await process_capture(ctx, capture_id, SEED_USER_ID)

	assert transcriber.calls == 1
	assert len(await _runs(admin_engine, capture_id)) == 3


async def test_retryable_failure_schedules_a_retry_and_keeps_the_stage(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-3")
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(fail_with=RetryableError("HTTP 503")),
		object_store=store,
	)

	outcome = await process_capture(ctx, capture_id, SEED_USER_ID)

	assert outcome.retry_after is not None and outcome.retry_after >= 0
	row = await _capture_row(admin_engine, capture_id)
	assert row["status"] == "transcribing"
	assert row["attempt_count"] == 1
	assert (await _runs(admin_engine, capture_id))[0] == ("transcribe", "failed", 1)


async def test_attempt_cap_turns_retryable_into_failed(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-4")
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(fail_with=RetryableError("HTTP 503")),
		object_store=store,
		max_attempts=2,
	)

	first = await process_capture(ctx, capture_id, SEED_USER_ID)
	second = await process_capture(ctx, capture_id, SEED_USER_ID)

	assert first.retry_after is not None
	assert second.status == "failed" and second.retry_after is None
	row = await _capture_row(admin_engine, capture_id)
	assert row["status"] == "failed"
	assert "gave up after 2 attempts" in str(row["failure_reason"])
	assert (await _runs(admin_engine, capture_id))[0] == ("transcribe", "failed", 2)


async def test_terminal_failure_is_visible_immediately(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-5")
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(fail_with=TerminalError("HTTP 400: bad audio")),
		object_store=store,
	)

	outcome = await process_capture(ctx, capture_id, SEED_USER_ID)

	assert outcome.status == "failed"
	row = await _capture_row(admin_engine, capture_id)
	assert row["failure_reason"] == "transcribe: HTTP 400: bad audio"
	assert row["attempt_count"] == 0


async def test_user_retry_after_failure_reruns_only_the_failed_stage(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-6")
	broken = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(fail_with=TerminalError("bad audio")),
		object_store=store,
	)
	await process_capture(broken, capture_id, SEED_USER_ID)
	assert (await client.post(f"/v1/captures/{capture_id}/retry")).status_code == 202

	fixed = FakeTranscriber("second time lucky")
	await process_capture(
		PipelineContext(session=session, transcriber=fixed, object_store=store),
		capture_id,
		SEED_USER_ID,
	)

	row = await _capture_row(admin_engine, capture_id)
	assert row["status"] == "ready" and row["text"] == "second time lucky"
	assert fixed.calls == 1
	assert (await _runs(admin_engine, capture_id))[0] == ("transcribe", "succeeded", 2)


async def test_text_capture_never_calls_the_transcriber(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	created = await client.post(
		"/v1/captures", json={"text": "typed, not spoken", "client_token": "tok-pipe-7"}
	)
	capture_id = uuid.UUID(created.json()["id"])
	transcriber = FakeTranscriber()

	outcome = await process_capture(
		PipelineContext(session=session, transcriber=transcriber, object_store=store),
		capture_id,
		SEED_USER_ID,
	)

	assert outcome.status == "ready"
	assert transcriber.calls == 0
	assert [r[0] for r in await _runs(admin_engine, capture_id)] == ["extract", "embed"]


async def test_unknown_capture_asks_for_a_short_retry(
	session: AsyncSession, store: FakeObjectStore
) -> None:
	outcome = await process_capture(
		PipelineContext(
			session=session, transcriber=FakeTranscriber(), object_store=store
		),
		uuid.uuid4(),
		SEED_USER_ID,
	)
	assert outcome.status == "missing" and outcome.retry_after == 1.0
