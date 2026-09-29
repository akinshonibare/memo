"""Auto-accept (ADR 0030): keep what extraction finds without asking, off by default,
stored as 'accepted' so it is never mistaken for something a person checked."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from evals.extraction.harvest import candidate
from memo.config import SEED_USER_ID
from memo.prompts.answer_v2 import extracted_line
from memo.schemas.derivations import Extraction, IdeaPayload, TodoPayload
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.pipeline import PipelineContext, process_capture
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


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	extraction: Extraction,
	people: PeopleExtraction | None = None,
) -> dict[str, Any]:
	created = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	capture_id = uuid.UUID(created.json()["id"])
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(people),
	)
	assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"
	note: dict[str, Any] = (await client.get(f"/v1/captures/{capture_id}")).json()
	return note


async def test_off_by_default_and_a_real_boolean(client: AsyncClient) -> None:
	assert (await client.get("/v1/me")).json()["auto_accept"] is False
	on = await client.patch("/v1/me", json={"auto_accept": True})
	assert on.status_code == 200 and on.json()["auto_accept"] is True
	assert (await client.patch("/v1/me", json={"auto_accept": None})).status_code == 422


async def test_off_items_wait_for_review(
	client: AsyncClient, session: AsyncSession
) -> None:
	note = await _note(
		client, session, "call Tunde", Extraction(todos=[TodoPayload(title="Call")])
	)
	assert [d["status"] for d in note["derivations"]] == ["pending_review"]


async def test_on_items_are_kept_as_accepted_not_confirmed(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"auto_accept": True})
	await _note(
		client,
		session,
		"two Tundes",
		Extraction(),
		PeopleExtraction(
			people=[
				MentionPayload(name="Tunde", full_name="Tunde Bakare"),
				MentionPayload(name="Tunde", full_name="Tunde Ade"),
			]
		),
	)
	note = await _note(
		client,
		session,
		"call Tunde, and Kemi said hi",
		Extraction(todos=[TodoPayload(title="Call Tunde")]),
		PeopleExtraction(
			people=[MentionPayload(name="Tunde"), MentionPayload(name="Kemi")]
		),
	)
	(todo,) = note["derivations"]
	assert todo["status"] == "accepted"
	assert todo["reviewed_at"] is None  # kept, but nobody looked at it
	people = {m["name"]: m for m in note["people"]}
	assert people["Kemi"]["status"] == "accepted"  # new, linked: kept
	# Two Tundes: the setting trusts the reading, it does not guess who.
	assert people["Tunde"]["status"] == "pending_review"
	assert people["Tunde"]["person"] is None
	assert len((await client.get("/v1/people/unresolved")).json()) == 1

	# Editing one is a person looking at it: now it is confirmed.
	edited = await client.patch(
		f"/v1/derivations/{todo['id']}",
		json={"payload": {"title": "Call Tunde about pricing"}},
	)
	assert edited.json()["status"] == "confirmed"

	# Turning it off is about the future, not a review of the past.
	await client.patch("/v1/me", json={"auto_accept": False})
	later = await _note(
		client, session, "buy milk", Extraction(todos=[TodoPayload(title="Milk")])
	)
	assert later["derivations"][0]["status"] == "pending_review"


def test_ask_trusts_an_accepted_item_without_hedging() -> None:
	line = extracted_line(
		"todo",
		{"title": "Call Tunde", "due": "2026-10-01"},
		"accepted",
		dt.UTC,  # type: ignore[arg-type]
	)
	assert line == "todo: Call Tunde · due Thu 1 Oct 2026 (accepted automatically)"


def _exported(*derivations: dict[str, Any]) -> dict[str, Any]:
	return {
		"id": "01a0eb16-0000-7000-8000-000000000001",
		"text": "call Tunde tomorrow",
		"status": "ready",
		"created_at": "2026-09-28T09:00:00+01:00",
		"derivations": list(derivations),
	}


def test_the_harvester_never_labels_what_nobody_checked() -> None:
	todo = {"kind": "todo", "payload": {"title": "Call Tunde", "due": None}}
	accepted = _exported({**todo, "status": "accepted"})
	assert candidate(accepted, "Europe/London") is None
	confirmed = _exported({**todo, "status": "confirmed"})
	assert candidate(confirmed, "Europe/London") is not None


def test_the_harvester_ignores_mentions() -> None:
	"""An export now carries mentions (ADR 0029); the harvester once looked up an
	extraction model for every kind and crashed on them."""
	todo = {
		"kind": "todo",
		"status": "confirmed",
		"payload": {"title": "Call Tunde", "due": None},
	}
	mention = {
		"kind": "mention",
		"status": "pending_review",
		"payload": {"name": "Tunde", "contact": "none"},
	}
	case = candidate(_exported(todo, mention), "Europe/London")
	assert case is not None
	assert case["expected"] == {"todos": [{"title": "Call Tunde"}]}


async def test_accepted_ideas_still_resurface(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	created = await client.post(
		"/v1/captures", json={"text": "an idea", "client_token": "tok-old-idea"}
	)
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO derivations (id, user_id, capture_id, kind, payload, "
				"status, extractor_version, created_at, updated_at) VALUES "
				"(:id, :u, :c, 'idea', CAST(:p AS jsonb), 'accepted', 'x', "
				"now() - interval '10 days', now())"
			),
			{
				"id": uuid.uuid4(),
				"u": SEED_USER_ID,
				"c": created.json()["id"],
				"p": IdeaPayload(
					title="Ration", summary="One a day."
				).model_dump_json(),
			},
		)
	morning = (await client.get("/v1/morning")).json()
	assert morning["resurfaced_idea"]["title"] == "Ration"
