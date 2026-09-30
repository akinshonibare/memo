"""Lately (ADR 0062): a line at the top of a person's page, written ahead by a sweep
from the notes that mention them, and rewritten when those notes or the tone change."""

import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_system, bind_user
from memo.jobs import refresh_lately_task
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.lately import NOTES_READ, stale_people
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import TerminalError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeLatelyWriter,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

KEMI = "Got the Flutterwave job and is nervous about Saturday—the flat viewing."


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _note(
	client: AsyncClient, session: AsyncSession, body: str, *mentions: dict[str, Any]
) -> str:
	made = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"t-{uuid.uuid4()}"}
	)
	cid = made.json()["id"]
	reader = FakePeopleReader(
		PeopleExtraction(people=[MentionPayload.model_validate(m) for m in mentions])
	)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=reader,
	)
	assert (await process_capture(ctx, uuid.UUID(cid), SEED_USER_ID)).status == "ready"
	return str(cid)


async def _sweep(app: FastAPI, writer: FakeLatelyWriter) -> int:
	return await refresh_lately_task(
		{"session_factory": app.state.session_factory, "lately": writer}
	)


async def _stale(app: FastAPI) -> list[tuple[uuid.UUID, uuid.UUID]]:
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		return await stale_people(s, limit=50)


async def _kemi(client: AsyncClient) -> dict[str, Any]:
	people = (await client.get("/v1/people")).json()["people"]
	found: dict[str, Any] = next(p for p in people if p["name"] == "Kemi")
	return found


async def test_a_line_is_written_ahead_from_the_newest_notes_in_your_tone(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
) -> None:
	await client.patch("/v1/me", json={"tone": "warm"})
	for n in range(NOTES_READ + 2):
		await _note(
			client,
			session,
			f"note {n} about kemi",
			{"name": "Kemi", "contact": "call", "context": f"thing {n}"},
		)
	kemi = await _kemi(client)
	assert kemi["lately"] is None and kemi["lately_at"] is None
	assert [p for p, _ in await _stale(app)] == [uuid.UUID(kemi["id"])]

	writer = FakeLatelyWriter(KEMI)
	assert await _sweep(app, writer) == 1
	name, notes, tone = writer.seen[0]
	assert (name, tone) == ("Kemi", "warm")
	# The newest few, newest first; last year's news can't crowd out this week's.
	assert len(notes) == NOTES_READ
	assert notes[0].about == f"thing {NOTES_READ + 1}"
	assert notes[0].in_touch == "spoke on the phone"

	page = (await client.get(f"/v1/people/{kemi['id']}")).json()
	# No em dashes in what memo writes (ADR 0061).
	assert page["lately"] == (
		"Got the Flutterwave job and is nervous about Saturday, the flat viewing."
	)
	assert page["lately_at"] is not None
	assert await _stale(app) == []
	assert await _sweep(app, writer) == 0 and writer.calls == 1, "nothing changed"
	async with admin_engine.connect() as conn:
		assert (
			await conn.scalar(
				text("SELECT count(*) FROM llm_calls WHERE stage='lately'")
			)
			== 1
		)


async def test_a_new_note_a_deleted_note_a_new_tone_or_a_new_name_rewrite_it(
	app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
	first = await _note(client, session, "lunch with kemi", {"name": "Kemi"})
	writer = FakeLatelyWriter("Lunch together.")
	await _sweep(app, writer)
	kemi = await _kemi(client)

	second = await _note(client, session, "kemi called", {"name": "Kemi"})
	assert len(await _stale(app)) == 1
	await _sweep(app, writer)

	assert (await client.delete(f"/v1/captures/{second}")).status_code == 204
	assert len(await _stale(app)) == 1, "a deleted note may have been in the line"
	await _sweep(app, writer)

	await client.patch("/v1/me", json={"tone": "brief"})
	assert len(await _stale(app)) == 1
	await _sweep(app, writer)
	assert writer.seen[-1][2] == "brief"

	await client.patch(f"/v1/people/{kemi['id']}", json={"name": "Kemi Adeyemi"})
	assert len(await _stale(app)) == 1
	await _sweep(app, writer)
	assert writer.seen[-1][0] == "Kemi Adeyemi"

	# Every note gone: the line goes too, without a model call.
	calls = writer.calls
	assert (await client.delete(f"/v1/captures/{first}")).status_code == 204
	await _sweep(app, writer)
	page = (await client.get(f"/v1/people/{kemi['id']}")).json()
	assert page["lately"] is None and writer.calls == calls


async def test_nothing_to_say_is_a_quiet_page_and_a_failure_is_not_retried_forever(
	app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "saw kemi at church", {"name": "Kemi"})
	await _sweep(app, FakeLatelyWriter(""))
	assert (await _kemi(client))["lately"] is None
	assert await _stale(app) == []

	await _note(client, session, "kemi again", {"name": "Kemi"})
	broken = FakeLatelyWriter(fail_with=TerminalError("refused"))
	await _sweep(app, broken)
	assert await _stale(app) == [], "stamped, so the sweep doesn't pay to fail again"
	await _sweep(app, broken)
	assert broken.calls == 1


async def test_the_sweep_runs_across_users_and_writes_each_as_its_owner(
	app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
	# The sweep runs as the system across users, and writes each as its owner.
	await _note(client, session, "kemi", {"name": "Kemi"})
	await _sweep(app, FakeLatelyWriter("Kemi's line."))
	async with app.state.session_factory() as s:
		await bind_user(s, uuid.uuid4())
		assert (await s.execute(text("SELECT count(*) FROM people"))).scalar() == 0
	assert (await _kemi(client))["lately"] == "Kemi's line."


def test_the_line_is_short() -> None:
	from pydantic import ValidationError

	from memo.schemas.people import LatelyWrite

	assert LatelyWrite(text="  Two   spaces. ").text == "Two spaces."
	with pytest.raises(ValidationError):
		LatelyWrite(text="x" * 400)
