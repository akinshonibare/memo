"""Search, related notes, the weekly review, and offline capture times (ADR 0028)."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import SEED_USER_ID, Settings, get_settings
from memo.db import bind_user
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import FakeEmbedder, FakeExtractor, FakeObjectStore, FakeTranscriber

NOTES = [
	"call Tunde about the pricing for the new plan",
	"Tunde said the pricing plan needs a discount tier",
	"the dentist moved to Friday at half two",
]


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _notes(client: AsyncClient, session: AsyncSession) -> list[str]:
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
	)
	ids = []
	for i, body in enumerate(NOTES):
		made = await client.post(
			"/v1/captures", json={"text": body, "client_token": f"tok-srr-{i}"}
		)
		cid = made.json()["id"]
		await process_capture(ctx, uuid.UUID(cid), SEED_USER_ID)
		ids.append(cid)
	return ids


async def test_search_ranks_by_meaning_and_returns_notes(
	client: AsyncClient, session: AsyncSession
) -> None:
	ids = await _notes(client, session)
	hits = (await client.get("/v1/search", params={"q": "dentist appointment"})).json()
	assert hits and hits[0]["capture"]["id"] == ids[2]
	assert "dentist" in hits[0]["snippet"]
	assert (await client.get("/v1/search", params={"q": "a"})).status_code == 422


async def test_related_notes_are_the_nearest_others(
	app: FastAPI, client: AsyncClient, session: AsyncSession, settings: Settings
) -> None:
	ids = await _notes(client, session)
	# The fake embedder isn't calibrated like voyage-4; widen the gate for the order.
	app.dependency_overrides[get_settings] = lambda: settings.model_copy(
		update={"related_max_distance": 2.0}
	)
	related = (await client.get(f"/v1/captures/{ids[0]}/related")).json()
	assert related[0]["capture"]["id"] == ids[1], "the other Tunde note first"
	assert ids[0] not in {r["capture"]["id"] for r in related}, "never itself"

	app.dependency_overrides[get_settings] = lambda: settings.model_copy(
		update={"related_max_distance": 0.0}
	)
	quiet = (await client.get(f"/v1/captures/{ids[0]}/related")).json()
	assert quiet == [], "nothing close enough means nothing shown"


async def test_the_week_collects_done_open_coming_and_ideas(
	client: AsyncClient,
) -> None:
	soon = (dt.datetime.now(dt.UTC) + dt.timedelta(days=2)).isoformat()
	await client.post("/v1/captures", json={"text": "x", "client_token": "tok-week-1"})
	done = (
		await client.post(
			"/v1/derivations", json={"kind": "todo", "payload": {"title": "Pay rent"}}
		)
	).json()
	await client.patch(f"/v1/derivations/{done['id']}", json={"completed": True})
	await client.post(
		"/v1/derivations",
		json={"kind": "todo", "payload": {"title": "Book MOT", "due": "2026-10-01"}},
	)
	await client.post(
		"/v1/derivations", json={"kind": "todo", "payload": {"title": "Undated"}}
	)
	await client.post(
		"/v1/derivations",
		json={"kind": "event", "payload": {"title": "Dentist", "starts_at": soon}},
	)
	await client.post(
		"/v1/derivations",
		json={"kind": "idea", "payload": {"title": "Seat pricing", "summary": "s"}},
	)

	week = (await client.get("/v1/review/week")).json()
	assert week["captured"] == {"notes": 1, "voice": 0, "photos": 0}
	assert [d["payload"]["title"] for d in week["done"]] == ["Pay rent"]
	assert [d["payload"]["title"] for d in week["still_open"]] == [
		"Book MOT",
		"Undated",
	]
	assert [d["payload"]["title"] for d in week["coming_up"]] == ["Dentist"]
	assert [d["payload"]["title"] for d in week["new_ideas"]] == ["Seat pricing"]
	start = dt.date.fromisoformat(week["start"])
	assert start.weekday() == 0 and week["end"] == str(start + dt.timedelta(days=6))


async def test_an_offline_note_keeps_the_time_it_was_taken(client: AsyncClient) -> None:
	taken = dt.datetime.now(dt.UTC) - dt.timedelta(days=2, hours=3)
	body = {
		"text": "later",
		"client_token": "tok-offline-1",
		"captured_at": taken.isoformat(),
	}
	first = await client.post("/v1/captures", json=body)
	assert first.status_code == 202, first.text
	assert dt.datetime.fromisoformat(first.json()["created_at"]) == taken
	replay = await client.post("/v1/captures", json=body)
	assert replay.json()["id"] == first.json()["id"], "a retried sync is not a new note"

	future = dt.datetime.now(dt.UTC) + dt.timedelta(hours=1)
	refused = await client.post(
		"/v1/captures",
		json={
			"text": "x",
			"client_token": "tok-offline-2",
			"captured_at": future.isoformat(),
		},
	)
	assert refused.status_code == 422
