"""process_capture called directly - no arq, no Redis (issue #12's done-when)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.schemas.derivations import Extraction
from memo.services.extraction import ExtractionFailed
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import RetryableError, TerminalError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
	fake_llm_call,
)


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


def _ctx(
	session: AsyncSession,
	store: FakeObjectStore,
	*,
	transcriber: FakeTranscriber | None = None,
	extractor: FakeExtractor | None = None,
	embedder: FakeEmbedder | None = None,
	max_attempts: int = 5,
) -> PipelineContext:
	return PipelineContext(
		session=session,
		transcriber=transcriber or FakeTranscriber(),
		object_store=store,
		extractor=extractor or FakeExtractor(),
		embedder=embedder or FakeEmbedder(),
		people=FakePeopleReader(),
		max_attempts=max_attempts,
	)


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


async def _text_capture(client: AsyncClient, text_: str, token: str) -> uuid.UUID:
	created = await client.post(
		"/v1/captures", json={"text": text_, "client_token": token}
	)
	assert created.status_code == 202, created.text
	return uuid.UUID(created.json()["id"])


async def _capture_row(
	admin_engine: AsyncEngine, capture_id: uuid.UUID
) -> dict[str, object]:
	async with admin_engine.connect() as conn:
		row = (
			(
				await conn.execute(
					text(
						"SELECT status, text, failure_reason, attempt_count, "
						"extraction_raw FROM captures WHERE id = :id"
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


async def _derivations(
	admin_engine: AsyncEngine, capture_id: uuid.UUID
) -> list[tuple[str, str, str]]:
	async with admin_engine.connect() as conn:
		rows = (
			await conn.execute(
				text(
					"SELECT kind, status, payload->>'title' FROM derivations "
					"WHERE capture_id = :id ORDER BY created_at, id"
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

	outcome = await process_capture(
		_ctx(session, store, transcriber=transcriber), capture_id, SEED_USER_ID
	)

	assert outcome.status == "ready" and outcome.retry_after is None
	row = await _capture_row(admin_engine, capture_id)
	assert row["status"] == "ready"
	assert row["text"] == "remember to book the dentist"
	assert transcriber.calls == 1
	assert await _runs(admin_engine, capture_id) == [
		("transcribe", "succeeded", 1),
		("extract", "succeeded", 1),
		("people", "succeeded", 1),
		("embed", "succeeded", 1),
	]


async def test_running_twice_transcribes_and_extracts_once(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-2")
	transcriber, extractor = FakeTranscriber(), FakeExtractor()
	ctx = _ctx(session, store, transcriber=transcriber, extractor=extractor)

	await process_capture(ctx, capture_id, SEED_USER_ID)
	await process_capture(ctx, capture_id, SEED_USER_ID)

	assert transcriber.calls == 1
	assert extractor.calls == 1
	assert len(await _runs(admin_engine, capture_id)) == 4


async def test_retryable_failure_schedules_a_retry_and_keeps_the_stage(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _audio_capture(client, admin_engine, store, "tok-pipe-3")
	ctx = _ctx(
		session, store, transcriber=FakeTranscriber(fail_with=RetryableError("503"))
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
	ctx = _ctx(
		session,
		store,
		transcriber=FakeTranscriber(fail_with=RetryableError("HTTP 503")),
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
	ctx = _ctx(
		session,
		store,
		transcriber=FakeTranscriber(fail_with=TerminalError("HTTP 400: bad audio")),
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
	broken = _ctx(
		session, store, transcriber=FakeTranscriber(fail_with=TerminalError("bad"))
	)
	await process_capture(broken, capture_id, SEED_USER_ID)
	assert (await client.post(f"/v1/captures/{capture_id}/retry")).status_code == 202

	fixed = FakeTranscriber("second time lucky")
	await process_capture(
		_ctx(session, store, transcriber=fixed), capture_id, SEED_USER_ID
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
	capture_id = await _text_capture(client, "typed, not spoken", "tok-pipe-7")
	transcriber = FakeTranscriber()

	outcome = await process_capture(
		_ctx(session, store, transcriber=transcriber), capture_id, SEED_USER_ID
	)

	assert outcome.status == "ready"
	assert transcriber.calls == 0
	assert [r[0] for r in await _runs(admin_engine, capture_id)] == [
		"extract",
		"people",
		"embed",
	]


async def test_unknown_capture_asks_for_a_short_retry(
	session: AsyncSession, store: FakeObjectStore
) -> None:
	outcome = await process_capture(_ctx(session, store), uuid.uuid4(), SEED_USER_ID)
	assert outcome.status == "missing" and outcome.retry_after == 1.0


# --- extraction stage ------------------------------------------------------------


def _extraction() -> Extraction:
	return Extraction.model_validate(
		{
			"todos": [{"title": "Call Tunde about pricing", "confidence": 0.9}],
			"ideas": [
				{
					"title": "Ration resurfacing",
					"summary": "One idea a day.",
					"confidence": 0.7,
				}
			],
		}
	)


async def test_extraction_writes_derivations_and_llm_calls(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _text_capture(
		client, "call Tunde about pricing; maybe ration resurfacing", "tok-ext-1"
	)
	extractor = FakeExtractor(_extraction())

	outcome = await process_capture(
		_ctx(session, store, extractor=extractor), capture_id, SEED_USER_ID
	)

	assert outcome.status == "ready"
	assert extractor.seen[0][2] == "UTC"  # the seeded user's timezone
	assert await _derivations(admin_engine, capture_id) == [
		("todo", "pending_review", "Call Tunde about pricing"),
		("idea", "pending_review", "Ration resurfacing"),
	]
	async with admin_engine.connect() as conn:
		calls = (
			await conn.execute(
				text(
					"SELECT stage, model, cost_usd FROM llm_calls "
					"WHERE capture_id = :id ORDER BY created_at, id"
				),
				{"id": capture_id},
			)
		).all()
	# One call each for extraction and people (ADR 0029): cost is per stage.
	assert [c[0] for c in calls] == ["extract", "people"]
	assert calls[0][1] == "claude-opus-5"
	assert float(calls[0][2]) == pytest.approx(0.0035)
	shown = (await client.get(f"/v1/captures/{capture_id}")).json()
	assert [d["kind"] for d in shown["derivations"]] == ["todo", "idea"]
	assert shown["derivations"][0]["extractor_version"] == "extract-v1.1/fake"
	assert shown["derivations"][0]["confidence"] == 0.9


async def test_extraction_validation_failure_is_visible_and_keeps_the_raw(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _text_capture(client, "something", "tok-ext-2")
	failure = ExtractionFailed(
		"schema_validation after 2 attempts:\n- todos.0.title: blank",
		raw='{"todos": [{"title": "   "}]}',
		calls=[fake_llm_call(), fake_llm_call()],
	)

	outcome = await process_capture(
		_ctx(session, store, extractor=FakeExtractor(fail_with=failure)),
		capture_id,
		SEED_USER_ID,
	)

	assert outcome.status == "failed"
	row = await _capture_row(admin_engine, capture_id)
	assert str(row["failure_reason"]).startswith("extract: schema_validation")
	assert row["extraction_raw"] == '{"todos": [{"title": "   "}]}'
	async with admin_engine.connect() as conn:
		paid = await conn.scalar(
			text("SELECT count(*) FROM llm_calls WHERE capture_id = :id"),
			{"id": capture_id},
		)
	assert paid == 2, "failed attempts still cost money and are still recorded"


async def test_rerunning_extraction_replaces_only_unreviewed_rows(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	capture_id = await _text_capture(client, "x", "tok-ext-3")
	extractor = FakeExtractor(_extraction())
	ctx = _ctx(session, store, extractor=extractor)
	await process_capture(ctx, capture_id, SEED_USER_ID)

	# The user confirms the todo, then the extract stage is forced to run again
	# (as it would after a crash mid-stage, or a user retry of a later failure).
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE derivations SET status = 'confirmed' "
				"WHERE capture_id = :id AND kind = 'todo'"
			),
			{"id": capture_id},
		)
		await conn.execute(
			text(
				"UPDATE job_runs SET status = 'failed' "
				"WHERE capture_id = :id AND stage = 'extract'"
			),
			{"id": capture_id},
		)
		await conn.execute(
			text("UPDATE captures SET status = 'extracting' WHERE id = :id"),
			{"id": capture_id},
		)
	await process_capture(ctx, capture_id, SEED_USER_ID)

	assert extractor.calls == 2
	rows = await _derivations(admin_engine, capture_id)
	assert ("todo", "confirmed", "Call Tunde about pricing") in rows
	assert rows.count(("idea", "pending_review", "Ration resurfacing")) == 1
	assert len(rows) == 3, (
		"confirmed row kept; unreviewed rows replaced, not duplicated"
	)


async def test_a_capture_with_no_words_is_ready_and_costs_nothing(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,
	store: FakeObjectStore,
) -> None:
	"""A voice note where nothing was said is not a failure: nothing to read, so no
	model call, and the note is ready with nothing extracted."""
	capture_id = await _audio_capture(client, admin_engine, store, "tok-ext-4")
	extractor, people, embedder = FakeExtractor(), FakePeopleReader(), FakeEmbedder()
	ctx = _ctx(
		session,
		store,
		transcriber=FakeTranscriber(text="   "),
		extractor=extractor,
		embedder=embedder,
	)
	ctx.people = people

	outcome = await process_capture(ctx, capture_id, SEED_USER_ID)

	assert outcome.status == "ready"
	row = await _capture_row(admin_engine, capture_id)
	assert row["status"] == "ready" and row["failure_reason"] is None
	assert (extractor.calls, people.calls, embedder.calls) == (0, 0, 0)
	async with admin_engine.connect() as conn:
		costed = await conn.scalar(
			text("SELECT count(*) FROM llm_calls WHERE capture_id = :id"),
			{"id": capture_id},
		)
	assert costed == 0
