"""Mood (ADR 0058): how you sounded in your notes, on the Within screen. Read only with
Within (Faith) on; a guess, never an item; your own word for a day outranks it."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_system, bind_user
from memo.schemas.mood import MoodDayOut, MoodRead
from memo.services.mood import _round, summary, unread_for_mood
from memo.services.pipeline import (
	PipelineContext,
	process_capture,
	read_mood_in_old_note,
)
from memo.services.retry import RetryableError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeMoodReader,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

TODAY = dt.datetime.now(dt.UTC).date()  # the seed user is on UTC

SHATTERED = "Long day. The launch slipped again and I'm shattered."
FLAT = "WE GOT THE FLAT!! can't stop smiling"
ERRAND = "dentist Tuesday 3pm, buy milk"


def heavy(evidence: str = "I'm shattered", confidence: float = 0.9) -> MoodRead:
	return MoodRead(
		valence=-1, words=["tired"], evidence=evidence, confidence=confidence
	)


def bright(confidence: float = 0.9) -> MoodRead:
	return MoodRead(
		valence=2,
		words=["excited", "grateful"],
		evidence="WE GOT THE FLAT!!",
		confidence=confidence,
	)


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


def _ctx(session: AsyncSession, mood: FakeMoodReader) -> PipelineContext:
	return PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
		mood=mood,
	)


async def _note(
	client: AsyncClient, session: AsyncSession, body: str, mood: FakeMoodReader
) -> str:
	made = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	assert made.status_code == 202, made.text
	cid = made.json()["id"]
	outcome = await process_capture(_ctx(session, mood), uuid.UUID(cid), SEED_USER_ID)
	assert outcome.status == "ready", outcome
	return str(cid)


async def _within(client: AsyncClient, on: bool) -> None:
	r = await client.patch("/v1/me", json={"faith_enabled": on})
	assert r.status_code == 200 and r.json()["faith_enabled"] is on


async def _moods(admin: AsyncEngine) -> list[tuple[str, dict[str, object]]]:
	async with admin.connect() as conn:
		rows = await conn.execute(
			text(
				"SELECT kind, payload FROM derivations "
				"WHERE kind IN ('mood', 'mood_day') ORDER BY created_at"
			)
		)
		return [(r[0], r[1]) for r in rows.all()]


async def _backdate(admin: AsyncEngine, capture_id: str, days: int) -> None:
	async with admin.begin() as conn:
		await conn.execute(
			text(
				"UPDATE captures SET created_at = now() - make_interval(days => :d) "
				"WHERE id = :c"
			),
			{"d": days, "c": capture_id},
		)


# --- the read -------------------------------------------------------------------------


def test_a_read_says_nothing_else_when_the_note_shows_no_mood() -> None:
	assert MoodRead().valence is None
	with pytest.raises(ValidationError, match="valence is null"):
		MoodRead(valence=None, words=["tired"])
	with pytest.raises(ValidationError, match="needs the phrase"):
		MoodRead(valence=-1, words=["tired"])


def test_a_read_uses_everyday_words_never_clinical_ones() -> None:
	read = MoodRead(valence=-1, words=["Tired", "tired ", "anxious"], evidence="ugh")
	assert read.words == ["tired", "anxious"]
	for clinical in ("depressed", "anxiety", "manic"):
		with pytest.raises(ValidationError, match="everyday word"):
			MoodRead(valence=-2, words=[clinical], evidence="ugh")
	with pytest.raises(ValidationError):
		MoodRead(valence=3, evidence="ugh")
	with pytest.raises(ValidationError):
		MoodRead(valence=0, words=["a", "b", "c", "d"], evidence="ugh")


def test_halfway_never_rounds_quietly_to_steady() -> None:
	assert [_round(v) for v in (-1.5, -0.5, -0.3, 0.0, 0.3, 0.5, 1.5)] == [
		-2,
		-1,
		0,
		0,
		0,
		1,
		2,
	]


def _days(*valences: int | None) -> list[MoodDayOut]:
	first = dt.date(2026, 9, 21)  # a Monday
	return [
		MoodDayOut(
			day=first + dt.timedelta(days=n), valence=v, yours=False, words=[], notes=1
		)
		for n, v in enumerate(valences)
	]


def test_the_sentence_says_no_more_than_the_dots() -> None:
	assert summary(_days(None, None, None)) is None
	assert "starting" not in (summary(_days(0, 0, 0)) or "")
	assert summary(_days(None, 1, None)) == (
		"Only a day or two so far. There'll be a week to see soon."
	)
	assert summary(_days(0, 0, 0, 0, 0, 0, 0)) == "A steady week."
	# A steady Thursday is not "brighter" in a heavy week: only the far side is named.
	assert summary(_days(-1, -1, -1, 0, -1, 1, -1)) == (
		"A heavy week. Brighter on Saturday."
	)
	assert summary(_days(1, 1, -1, 2, 1, None, 1)) == (
		"A good week. Heavier on Wednesday."
	)
	assert (
		summary(_days(0, 1, 0, 0, None, 0, 0)) == "A steady week. Brighter on Tuesday."
	)
	# Both ways, two days or more each: never averaged into "steady".
	assert summary(_days(0, -1, 2, 2, -1, -1)) == (
		"An up-and-down week. Brighter on Wednesday and Thursday."
		" Heavier on Tuesday, Friday and Saturday."
	)


# --- the stage ------------------------------------------------------------------------


async def test_off_until_within_is_on_and_then_every_note_is_read(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	reader = FakeMoodReader(heavy())
	await _note(client, session, SHATTERED, reader)
	assert reader.calls == 0
	assert await _moods(admin_engine) == []
	async with admin_engine.connect() as conn:
		assert (
			await conn.scalar(
				text("SELECT count(*) FROM job_runs WHERE stage = 'mood'")
			)
			== 0
		), "off leaves no ledger row, so the backlog reads it later"
	assert (await client.get("/v1/faith")).json()["mood"] is None

	await _within(client, True)
	cid = await _note(client, session, SHATTERED, reader)
	assert reader.calls == 1
	assert await _moods(admin_engine) == [
		("mood", {"valence": -1, "words": ["tired"], "evidence": "I'm shattered"})
	]

	# Never an item, never on the note, never embedded.
	assert (await client.get("/v1/derivations")).json()["items"] == []
	detail = (await client.get(f"/v1/captures/{cid}")).json()
	assert detail["derivations"] == []
	async with admin_engine.connect() as conn:
		assert (
			await conn.scalar(
				text(
					"SELECT count(*) FROM chunks c JOIN derivations d "
					"ON d.id = c.derivation_id WHERE d.kind = 'mood'"
				)
			)
			== 0
		)


async def test_a_note_with_no_mood_leaves_nothing_and_a_made_up_quote_is_dropped(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _within(client, True)
	await _note(client, session, ERRAND, FakeMoodReader(MoodRead()))
	assert await _moods(admin_engine) == []

	await _note(
		client, session, SHATTERED, FakeMoodReader(heavy(evidence="I feel exhausted"))
	)
	((_, payload),) = await _moods(admin_engine)
	assert payload["valence"] == -1 and payload["evidence"] is None


async def test_the_week_on_the_within_screen(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _within(client, True)
	reader = FakeMoodReader(by_text={SHATTERED: heavy(), FLAT: bright(0.2)})
	await _note(client, session, SHATTERED, reader)
	await _note(client, session, FLAT, reader)
	yesterday = await _note(client, session, FLAT, FakeMoodReader(bright()))
	await _backdate(admin_engine, yesterday, 1)

	mood = (await client.get("/v1/faith")).json()["mood"]
	assert [d["day"] for d in mood["days"]] == [
		(TODAY - dt.timedelta(days=n)).isoformat() for n in range(6, -1, -1)
	]
	*_, day_before, today = mood["days"]
	assert day_before["valence"] == 2
	# A sure "heavy" and an unsure "bright": (-1 * 0.9 + 2 * 0.3) / 1.2 = -0.25.
	assert today == {
		"day": TODAY.isoformat(),
		"valence": 0,
		"yours": False,
		"words": ["tired", "excited", "grateful"],
		"notes": 2,
	}
	assert (
		mood["summary"] == "Only a day or two so far. There'll be a week to see soon."
	)


async def test_your_word_for_a_day_outranks_the_reads_and_can_be_taken_back(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _within(client, True)
	cid = await _note(client, session, SHATTERED, FakeMoodReader(heavy()))
	day = TODAY.isoformat()

	shown = (await client.get(f"/v1/faith/mood/{day}")).json()
	assert shown["day"]["valence"] == -1 and shown["day"]["yours"] is False
	assert [(r["capture_id"], r["title"], r["evidence"]) for r in shown["reads"]] == [
		(cid, SHATTERED, "I'm shattered")
	]

	said = await client.put(f"/v1/faith/mood/{day}", json={"valence": 1})
	assert said.status_code == 200, said.text
	assert said.json()["day"]["valence"] == 1 and said.json()["day"]["yours"] is True
	again = await client.put(f"/v1/faith/mood/{day}", json={"valence": 2})
	assert again.json()["day"]["valence"] == 2
	assert [k for k, _ in await _moods(admin_engine)].count("mood_day") == 1
	today = (await client.get("/v1/faith")).json()["mood"]["days"][-1]
	assert (today["valence"], today["yours"]) == (2, True)

	back = await client.delete(f"/v1/faith/mood/{day}")
	assert back.json()["day"]["valence"] == -1 and back.json()["day"]["yours"] is False

	tomorrow = (TODAY + dt.timedelta(days=1)).isoformat()
	for bad in (
		client.put(f"/v1/faith/mood/{tomorrow}", json={"valence": 1}),
		client.put("/v1/faith/mood/2001-01-01", json={"valence": 1}),
		client.put(f"/v1/faith/mood/{day}", json={"valence": 3}),
		client.get(f"/v1/faith/mood/{tomorrow}"),
	):
		assert (await bad).status_code == 422


async def test_deleting_a_note_takes_its_read_with_it(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _within(client, True)
	cid = await _note(client, session, SHATTERED, FakeMoodReader(heavy()))
	assert (await client.delete(f"/v1/captures/{cid}")).status_code == 204
	assert await _moods(admin_engine) == []
	today = (await client.get("/v1/faith")).json()["mood"]["days"][-1]
	assert today["valence"] is None


async def test_mood_is_the_users_own(
	client: AsyncClient,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await _within(client, True)
	await _note(client, session, SHATTERED, FakeMoodReader(heavy()))
	day = TODAY.isoformat()

	as_user(second_user)
	await _within(client, True)
	theirs = (await client.get(f"/v1/faith/mood/{day}")).json()
	assert theirs["reads"] == [] and theirs["day"]["valence"] is None
	assert (
		await client.put(f"/v1/faith/mood/{day}", json={"valence": 2})
	).status_code == 200

	as_user(SEED_USER_ID)
	mine = (await client.get(f"/v1/faith/mood/{day}")).json()
	assert (mine["day"]["valence"], mine["day"]["yours"]) == (-1, False)


# --- the backlog ----------------------------------------------------------------------


async def test_turning_within_on_reads_the_last_month(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
) -> None:
	nothing = FakeMoodReader(heavy())
	recent = await _note(client, session, SHATTERED, nothing)
	old = await _note(client, session, "a note from long ago", nothing)
	await _backdate(admin_engine, recent, 3)
	await _backdate(admin_engine, old, 45)
	assert nothing.calls == 0

	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_for_mood(s, limit=10) == [], "off: nothing to read"
	await _within(client, True)
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_for_mood(s, limit=10) == [(uuid.UUID(recent), SEED_USER_ID)]

	flaky = FakeMoodReader(fail_with=RetryableError("429"))
	assert (
		await read_mood_in_old_note(
			_ctx(session, flaky), uuid.UUID(recent), SEED_USER_ID
		)
		== "retry_later"
	)
	reader = FakeMoodReader(heavy())
	ctx = _ctx(session, reader)
	assert await read_mood_in_old_note(ctx, uuid.UUID(recent), SEED_USER_ID) == "read"
	assert await read_mood_in_old_note(ctx, uuid.UUID(recent), SEED_USER_ID) == "done"
	assert reader.calls == 1
	days = (await client.get("/v1/faith")).json()["mood"]["days"]
	assert days[-4]["valence"] == -1
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_for_mood(s, limit=10) == []
