"""A daily AI budget per person (ADR 0063): counted from the cost log since their own
midnight; at the limit notes still save and wait, and Ask and voice say when they're
back."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.jobs import refresh_lately_task
from memo.models import User
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.budget import budget_for, speak_call, transcribe_call
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeLatelyWriter,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

NEW_YORK = ZoneInfo("America/New_York")


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _limit(admin: AsyncEngine, usd: str | None) -> None:
	async with admin.begin() as conn:
		await conn.execute(
			text("UPDATE users SET daily_ai_limit_usd = :v WHERE id = :u"),
			{"v": usd, "u": SEED_USER_ID},
		)


async def _spent(admin: AsyncEngine, usd: str, at: dt.datetime | None = None) -> None:
	async with admin.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO llm_calls (id, user_id, stage, provider, model, "
				"input_tokens, output_tokens, latency_ms, cost_usd, created_at) "
				"VALUES (:id, :u, 'extract', 'anthropic', 'claude-opus-5', 1, 1, 1, "
				":c, COALESCE(CAST(:at AS timestamptz), now()))"
			),
			{"id": uuid.uuid4(), "u": SEED_USER_ID, "c": usd, "at": at},
		)


async def _user(session: AsyncSession) -> User:
	user = await session.get(User, SEED_USER_ID, populate_existing=True)
	assert user is not None
	return user


async def test_spend_counts_from_your_own_midnight_against_your_limit(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await client.patch("/v1/me", json={"timezone": "America/New_York"})
	now = dt.datetime(2026, 10, 1, 9, 0, tzinfo=NEW_YORK)
	await _spent(
		admin_engine, "0.40", dt.datetime(2026, 9, 30, 23, 59, tzinfo=NEW_YORK)
	)
	await _spent(admin_engine, "0.25", dt.datetime(2026, 10, 1, 0, 1, tzinfo=NEW_YORK))
	await _spent(admin_engine, "0.30", dt.datetime(2026, 10, 1, 8, 0, tzinfo=NEW_YORK))

	budget = await budget_for(session, await _user(session), now=now)
	assert budget.spent == Decimal("0.55"), "yesterday evening is yesterday's"
	assert budget.limit is None and not budget.over, "tests run with no default"
	assert budget.resets_at == dt.datetime(2026, 10, 2, 0, 0, tzinfo=NEW_YORK)

	await _limit(admin_engine, "0.50")
	budget = await budget_for(session, await _user(session), now=now)
	assert budget.limit == Decimal("0.50") and budget.over
	await _limit(admin_engine, "0")
	assert (await budget_for(session, await _user(session), now=now)).limit is None


async def test_at_the_limit_a_note_saves_and_waits_for_midnight(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _limit(admin_engine, "0.10")
	await _spent(admin_engine, "0.12")
	made = await client.post(
		"/v1/captures", json={"text": "call Tunde", "client_token": f"t-{uuid.uuid4()}"}
	)
	assert made.status_code == 202, "a note is never refused for the budget"
	cid = uuid.UUID(made.json()["id"])
	extractor = FakeExtractor()
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=extractor,
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)

	waiting = await process_capture(ctx, cid, SEED_USER_ID)
	assert waiting.status not in ("ready", "failed")
	assert waiting.retry_after is not None and waiting.retry_after > 0
	assert extractor.calls == 0
	async with admin_engine.connect() as conn:
		assert (
			await conn.scalar(
				text("SELECT count(*) FROM job_runs WHERE capture_id = :c"), {"c": cid}
			)
			== 0
		), "the claim is let go: not an attempt, not a failure"
		assert (
			await conn.scalar(
				text("SELECT attempt_count FROM captures WHERE id = :c"), {"c": cid}
			)
			== 0
		)

	await _limit(admin_engine, "0")  # midnight, as far as the budget is concerned
	assert (await process_capture(ctx, cid, SEED_USER_ID)).status == "ready"
	assert extractor.calls == 1


async def test_ask_and_voice_say_when_they_are_back(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await _limit(admin_engine, "0.10")
	await _spent(admin_engine, "0.10")
	conversation = (await client.post("/v1/conversations")).json()["id"]
	asked = await client.post(
		f"/v1/conversations/{conversation}/messages", json={"text": "what's on?"}
	)
	assert asked.status_code == 429
	assert "after midnight" in asked.json()["detail"]
	assert int(asked.headers["Retry-After"]) > 0
	for call in (
		client.post(
			"/v1/voice/transcribe",
			content=b"\x00\x01fake",
			headers={"Content-Type": "audio/m4a"},
		),
		client.post("/v1/voice/speak", json={"text": "Hello there."}),
	):
		assert (await call).status_code == 429

	today = (await client.get("/v1/usage/today")).json()
	assert today["spent_usd"] == "0.10" and today["limit_usd"] == "0.1000"
	assert today["over"] is True


async def test_voice_is_priced_into_the_day(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	assert transcribe_call("nova-3", 60).cost_usd == Decimal("0.004300")
	assert speak_call("aura-2", 1000).cost_usd == Decimal("0.030000")

	await client.post(
		"/v1/voice/transcribe",
		content=b"\x00\x01fake",
		headers={"Content-Type": "audio/m4a"},
	)
	await client.post("/v1/voice/speak", json={"text": "Hello there."})
	async with admin_engine.connect() as conn:
		rows = (
			await conn.execute(
				text(
					"SELECT stage, provider, cost_usd FROM llm_calls "
					"WHERE user_id = :u ORDER BY stage"
				),
				{"u": SEED_USER_ID},
			)
		).all()
	assert [(r[0], r[1]) for r in rows] == [
		("speak", "deepgram"),
		("transcribe", "deepgram"),
	]
	assert rows[0][2] == Decimal("0.000360")  # 12 characters of Aura-2


async def test_background_rewrites_wait_for_someone_at_their_limit(
	app: FastAPI, client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	made = await client.post(
		"/v1/captures", json={"text": "saw kemi", "client_token": f"t-{uuid.uuid4()}"}
	)
	cid = uuid.UUID(made.json()["id"])
	people = FakePeopleReader(
		PeopleExtraction(people=[MentionPayload.model_validate({"name": "Kemi"})])
	)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=people,
	)
	assert (await process_capture(ctx, cid, SEED_USER_ID)).status == "ready"

	await _limit(admin_engine, "0.01")
	await _spent(admin_engine, "0.05")
	writer = FakeLatelyWriter("Kemi's line.")
	sweep = {"session_factory": app.state.session_factory, "lately": writer}
	await refresh_lately_task(sweep)
	assert writer.calls == 0

	await _limit(admin_engine, "0")
	await refresh_lately_task(sweep)
	assert writer.calls == 1
