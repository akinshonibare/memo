"""The map of one thing (ADR 0049): each kind of centre, the cap, what stays hidden,
and whose map it is."""

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
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
		await bind_user(s, SEED_USER_ID)
		yield s


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	*,
	people: list[str] = (),  # type: ignore[assignment]
	extraction: Extraction | None = None,
	space: str | None = None,
) -> str:
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(
			PeopleExtraction(people=[MentionPayload(name=n) for n in people])
		),
	)
	payload: dict[str, Any] = {"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	if space:
		payload["space"] = space
	created = await client.post("/v1/captures", json=payload)
	assert created.status_code == 202, created.text
	capture_id = uuid.UUID(created.json()["id"])
	assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"
	return str(capture_id)


async def _map(client: AsyncClient, kind: str, node_id: str) -> dict[str, Any]:
	got = await client.get(f"/v1/map?kind={kind}&id={node_id}")
	assert got.status_code == 200, got.text
	body: dict[str, Any] = got.json()
	return body


def _links(body: dict[str, Any]) -> list[tuple[str, str, str]]:
	return [
		(ln["relation"], ln["node"]["kind"], ln["node"]["label"])
		for ln in body["links"]
	]


async def _person(client: AsyncClient, name: str) -> str:
	people = (await client.get("/v1/people")).json()["people"]
	(match,) = [p["id"] for p in people if p["name"] == name]
	return str(match)


async def test_a_persons_map_has_their_notes_prayers_and_people(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "coffee with Kemi and Tunde", people=["Kemi", "Tunde"])
	await _note(client, session, "Kemi called about the flat", people=["Kemi"])
	kemi = await _person(client, "Kemi")
	await client.patch("/v1/me", json={"faith_enabled": True})
	prayed = await client.post(
		"/v1/faith/prayers", json={"title": "Kemi's exams", "person_id": kemi}
	)
	assert prayed.status_code == 201, prayed.text

	body = await _map(client, "person", kemi)

	assert body["center"]["label"] == "Kemi" and body["center"]["centrable"]
	links = _links(body)
	assert ("note", "note", "Kemi called about the flat") in links
	assert ("prayer for", "prayer", "Kemi's exams") in links
	assert ("with", "person", "Tunde") in links


async def test_a_notes_map_has_who_its_about_what_came_of_it_and_related_notes(
	client: AsyncClient, session: AsyncSession
) -> None:
	first = await _note(
		client,
		session,
		"pricing call with Tunde: per seat pricing idea, send the deck",
		people=["Tunde"],
		extraction=Extraction(
			todos=[TodoPayload(title="Send Tunde the deck")],
			ideas=[IdeaPayload(title="Per seat pricing", summary="Charge per seat.")],
		),
	)
	await _note(client, session, "more on pricing: per seat pricing with Tunde")

	links = _links(await _map(client, "note", first))

	assert ("about", "person", "Tunde") in links
	assert ("from it", "todo", "Send Tunde the deck") in links
	assert ("from it", "idea", "Per seat pricing") in links
	assert any(r == "related" and k == "note" for r, k, _ in links)


async def test_an_ideas_and_a_prayers_maps_show_their_threads(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	source = await _note(
		client,
		session,
		"idea: one old note each morning",
		extraction=Extraction(
			ideas=[IdeaPayload(title="One old note each morning", summary="Rationed.")]
		),
	)
	later = await _note(client, session, "make it weekly, not daily")
	await client.patch("/v1/me", json={"faith_enabled": True})
	await _note(client, session, "coffee with Kemi", people=["Kemi"])
	kemi = await _person(client, "Kemi")
	prayer = (
		await client.post(
			"/v1/faith/prayers", json={"title": "Kemi's exams", "person_id": kemi}
		)
	).json()["id"]
	async with admin_engine.begin() as conn:
		idea = await conn.scalar(
			text("SELECT id FROM derivations WHERE kind = 'idea' AND capture_id = :c"),
			{"c": source},
		)
		for target, relation in ((idea, "continues"), (prayer, "answers")):
			await conn.execute(
				text(
					"INSERT INTO derivations (id, user_id, capture_id, kind, payload, "
					"status, extractor_version, target_id) VALUES (:id, :u, :c, "
					"'update', CAST(:p AS jsonb), 'confirmed', 'test', :t)"
				),
				{
					"id": uuid.uuid4(),
					"u": SEED_USER_ID,
					"c": later,
					"p": f'{{"relation": "{relation}", "quote": "weekly"}}',
					"t": target,
				},
			)

	idea_links = _links(await _map(client, "idea", str(idea)))
	prayer_links = _links(await _map(client, "prayer", prayer))

	assert ("written in", "note", "idea: one old note each morning") in idea_links
	assert ("carried on", "note", "make it weekly, not daily") in idea_links
	assert ("for", "person", "Kemi") in prayer_links
	assert ("answered in", "note", "make it weekly, not daily") in prayer_links


async def test_a_busy_person_still_shows_every_kind_of_link_within_the_cap(
	client: AsyncClient, session: AsyncSession
) -> None:
	for i in range(12):
		await _note(
			client, session, f"saw Ada and Bola, day {i}", people=["Ada", "Bola"]
		)
	ada = await _person(client, "Ada")
	await client.patch("/v1/me", json={"faith_enabled": True})
	await client.post(
		"/v1/faith/prayers", json={"title": "Ada's move", "person_id": ada}
	)

	links = _links(await _map(client, "person", ada))

	assert len(links) <= 14
	kinds = {r for r, _, _ in links}
	assert kinds == {"note", "prayer for", "with"}
	assert sum(1 for r, _, _ in links if r == "note") == 8


async def test_a_faith_note_stays_off_the_map_while_faith_is_on(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	await _note(
		client, session, "prayed with Kemi tonight", people=["Kemi"], space="faith"
	)
	await _note(client, session, "lunch with Kemi", people=["Kemi"])
	kemi = await _person(client, "Kemi")

	labels = [label for _, _, label in _links(await _map(client, "person", kemi))]

	assert labels == ["lunch with Kemi"]


async def test_a_map_is_only_its_owners(
	client: AsyncClient,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	note = await _note(client, session, "coffee with Kemi", people=["Kemi"])
	as_user(second_user)
	assert (await client.get(f"/v1/map?kind=note&id={note}")).status_code == 404
	assert (await client.get(f"/v1/map?kind=planet&id={note}")).status_code == 422
