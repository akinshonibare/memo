"""Adds and edits keep a derivation's own chunk current (ADR 0021's open gap)."""

import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.services.derivation_index import reembed_derivation
from tests.fakes import FakeEmbedder, FakeQueue


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _capture(client: AsyncClient, token: str) -> str:
	created = await client.post(
		"/v1/captures", json={"text": "call Tunde", "client_token": token}
	)
	capture_id: str = created.json()["id"]
	return capture_id


async def _live_chunks(engine: AsyncEngine, derivation_id: str) -> list[str]:
	async with engine.connect() as conn:
		rows = await conn.execute(
			text(
				"SELECT text FROM chunks WHERE derivation_id = :d "
				"AND superseded_at IS NULL"
			),
			{"d": derivation_id},
		)
		return [r[0] for r in rows]


async def test_add_and_edit_enqueue_a_reembed_and_review_does_not(
	client: AsyncClient, queue: FakeQueue
) -> None:
	capture_id = await _capture(client, "tok-reembed-1")
	added = (
		await client.post(
			f"/v1/captures/{capture_id}/derivations",
			json={"kind": "todo", "payload": {"title": "Call Tunde"}},
		)
	).json()
	await client.patch(
		f"/v1/derivations/{added['id']}", json={"payload": {"title": "Call Tunde now"}}
	)
	await client.patch(f"/v1/derivations/{added['id']}", json={"status": "confirmed"})
	await client.patch(f"/v1/derivations/{added['id']}", json={"status": "rejected"})

	assert [str(d) for d, _, _ in queue.embeds] == [added["id"], added["id"]]
	fingerprints = {f for _, _, f in queue.embeds}
	assert len(fingerprints) == 2, "new text, new job id: an edit is never deduplicated"


async def test_reembed_creates_supersedes_and_is_idempotent(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
) -> None:
	capture_id = await _capture(client, "tok-reembed-2")
	added = (
		await client.post(
			f"/v1/captures/{capture_id}/derivations",
			json={
				"kind": "todo",
				"payload": {"title": "Call Tunde", "due": "2026-10-01"},
			},
		)
	).json()
	derivation_id = uuid.UUID(added["id"])
	embedder = FakeEmbedder()

	first = await reembed_derivation(session, embedder, derivation_id, SEED_USER_ID)
	await session.commit()
	await bind_user(session, SEED_USER_ID)
	again = await reembed_derivation(session, embedder, derivation_id, SEED_USER_ID)
	assert (first, again) == ("embedded", "unchanged")
	assert await _live_chunks(admin_engine, added["id"]) == [
		"todo: Call Tunde. due 2026-10-01"
	]

	await client.patch(
		f"/v1/derivations/{added['id']}",
		json={"payload": {"title": "Call Tunde about pricing", "due": "2026-10-02"}},
	)
	async with app.state.session_factory() as fresh:
		await bind_user(fresh, SEED_USER_ID)
		edited = await reembed_derivation(fresh, embedder, derivation_id, SEED_USER_ID)
		await fresh.commit()
	assert edited == "embedded"
	assert await _live_chunks(admin_engine, added["id"]) == [
		"todo: Call Tunde about pricing. due 2026-10-02"
	]

	await client.patch(f"/v1/derivations/{added['id']}", json={"status": "rejected"})
	assert await _live_chunks(admin_engine, added["id"]) == []
