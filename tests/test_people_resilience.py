"""Fixes from the review of #67 (ADR 0029): the people stage never costs a note its
place in search, the backlog tells a flaky provider from a broken note, and a merge or a
relink keeps one mention per person per note."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from evals.people.run import load_golden
from evals.people.run import run as run_people_eval
from memo.config import SEED_USER_ID
from memo.models import Person
from memo.schemas.people import MAX_ALIASES, MentionPayload, PeopleExtraction
from memo.services.people import BACKLOG_MAX_ATTEMPTS, with_alias
from memo.services.pipeline import (
	PipelineContext,
	process_capture,
	read_people_in_old_note,
)
from memo.services.retry import RetryableError, TerminalError
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
		yield s


def _ctx(session: AsyncSession, people: FakePeopleReader) -> PipelineContext:
	return PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=people,
	)


async def _capture(client: AsyncClient, body: str) -> uuid.UUID:
	created = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	return uuid.UUID(created.json()["id"])


async def _people_run(admin: AsyncEngine, capture_id: uuid.UUID) -> tuple | None:  # type: ignore[type-arg]
	async with admin.connect() as conn:
		row = (
			await conn.execute(
				text(
					"SELECT status, attempt FROM job_runs "
					"WHERE capture_id = :c AND stage = 'people'"
				),
				{"c": capture_id},
			)
		).first()
	return tuple(row) if row else None


@pytest.mark.parametrize(
	("error", "run_left"),
	[
		(TerminalError("model refused"), ("failed", 1)),
		# A 429 is not an attempt: no ledger row, so the note is simply still unread.
		(RetryableError("anthropic 429: slow down"), None),
	],
)
async def test_a_failed_people_read_still_leaves_a_searchable_note(
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
	error: Exception,
	run_left: tuple | None,  # type: ignore[type-arg]
) -> None:
	capture_id = await _capture(client, "lunch with Kemi about the flat")
	outcome = await process_capture(
		_ctx(session, FakePeopleReader(fail_with=error)), capture_id, SEED_USER_ID
	)

	assert outcome.status == "ready"
	assert outcome.retry_after is None
	hits = (await client.post("/v1/search", json={"q": "Kemi flat"})).json()
	assert any(h["capture"] and h["capture"]["id"] == str(capture_id) for h in hits)
	assert await _people_run(admin_engine, capture_id) == run_left
	assert (await client.get("/v1/people")).json()["unread_notes"] == 1


async def test_the_backlog_tells_a_flaky_provider_from_a_broken_note(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	capture_id = await _capture(client, "texted Kemi")
	await process_capture(
		_ctx(session, FakePeopleReader(fail_with=RetryableError("HTTP 503"))),
		capture_id,
		SEED_USER_ID,
	)

	flaky = FakePeopleReader(fail_with=RetryableError("HTTP 503"))
	for _ in range(BACKLOG_MAX_ATTEMPTS + 2):
		outcome = await read_people_in_old_note(
			_ctx(session, flaky), capture_id, SEED_USER_ID
		)
		assert outcome == "retry_later"
	# However long the outage, the note is still waiting, not given up on.
	assert await _people_run(admin_engine, capture_id) is None
	listed = (await client.get("/v1/people")).json()
	assert (listed["unread_notes"], listed["unreadable_notes"]) == (1, 0)

	broken = FakePeopleReader(fail_with=TerminalError("model refused"))
	for _ in range(BACKLOG_MAX_ATTEMPTS):
		assert (
			await read_people_in_old_note(
				_ctx(session, broken), capture_id, SEED_USER_ID
			)
			== "failed"
		)
	listed = (await client.get("/v1/people")).json()
	# Given up on - and said so, rather than quietly leaving the "to go" count.
	assert (listed["unread_notes"], listed["unreadable_notes"]) == (0, 1)


async def test_a_bug_in_one_old_note_is_a_failed_attempt_not_a_stuck_sweep(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	capture_id = await _capture(client, "texted Kemi")
	await process_capture(
		_ctx(session, FakePeopleReader(fail_with=RetryableError("HTTP 503"))),
		capture_id,
		SEED_USER_ID,
	)
	buggy = FakePeopleReader(fail_with=RuntimeError("a bug"))
	outcome = await read_people_in_old_note(
		_ctx(session, buggy), capture_id, SEED_USER_ID
	)
	assert outcome == "failed"
	assert await _people_run(admin_engine, capture_id) == ("failed", 1)


async def _note(
	client: AsyncClient, session: AsyncSession, body: str, *people: MentionPayload
) -> uuid.UUID:
	capture_id = await _capture(client, body)
	await process_capture(
		_ctx(session, FakePeopleReader(PeopleExtraction(people=list(people)))),
		capture_id,
		SEED_USER_ID,
	)
	return capture_id


async def test_merging_two_people_named_in_one_note_leaves_one_mention(
	client: AsyncClient, session: AsyncSession
) -> None:
	capture_id = await _note(
		client,
		session,
		"Tunde, or T as everyone calls him, called",
		MentionPayload(name="Tunde", contact="none"),
		MentionPayload(name="T", contact="call"),
	)
	people = {p["name"]: p for p in (await client.get("/v1/people")).json()["people"]}
	assert set(people) == {"Tunde", "T"}

	merged = await client.post(
		f"/v1/people/{people['T']['id']}/merge", json={"into": people["Tunde"]["id"]}
	)

	assert merged.json()["mention_count"] == 1
	assert len(merged.json()["mentions"]) == 1
	(only,) = (await client.get(f"/v1/captures/{capture_id}")).json()["people"]
	assert only["contact"] == "call"  # the one that says they were in touch
	assert merged.json()["last_contact_on"] is not None


async def test_linking_to_someone_the_note_already_names_keeps_one_mention(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "about Tunde", MentionPayload(name="Tunde"))
	capture_id = await _note(
		client,
		session,
		"Tunde and Tunday",
		MentionPayload(name="Tunde", contact="none"),
		MentionPayload(name="Tunday", contact="in_person"),
	)
	note = (await client.get(f"/v1/captures/{capture_id}")).json()
	tunde = next(m for m in note["people"] if m["name"] == "Tunde")
	misheard = next(m for m in note["people"] if m["name"] == "Tunday")
	assert misheard["person"] is None  # "Tunday" is a question

	answer = await client.patch(
		f"/v1/mentions/{misheard['id']}", json={"person_id": tunde["person"]["id"]}
	)

	assert answer.status_code == 200, answer.text
	(only,) = (await client.get(f"/v1/captures/{capture_id}")).json()["people"]
	assert only["id"] == answer.json()["id"]
	assert only["contact"] == "in_person" and only["status"] == "confirmed"


def test_aliases_stop_growing_at_the_cap_a_patch_accepts() -> None:
	full = Person(
		id=uuid.uuid4(),
		user_id=SEED_USER_ID,
		name="Tunde",
		aliases=[f"alias {n}" for n in range(MAX_ALIASES)],
	)
	assert with_alias(full, "Mum") == full.aliases


async def test_a_capped_person_can_still_be_edited(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "Tunde", MentionPayload(name="Tunde"))
	(person,) = (await client.get("/v1/people")).json()["people"]
	aliases = [f"alias {n}" for n in range(MAX_ALIASES)]
	patched = await client.patch(
		f"/v1/people/{person['id']}", json={"aliases": aliases}
	)
	assert patched.status_code == 200, patched.text


def test_an_invalid_recorded_output_is_a_miss_not_a_crash(tmp_path: Path) -> None:
	examples = load_golden()[:2]
	replay = {examples[0].id: '{"people": [{"name": ""}]}', examples[1].id: "not json"}
	report = asyncio.run(run_people_eval(examples, None, replay, None))
	assert report.examples == 2
