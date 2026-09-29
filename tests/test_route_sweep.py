"""Every route, against a corpus with every kind of row the API now holds.

Added after 2026-09-29, when Ask crashed on a note with a linked person: each feature
had its own tests, but no test ever put a person *in front of an older route*. This one
builds that corpus - linked and unresolved people, auto-accepted items, a note with no
words, a deleted note whose items and mention outlive it, a contact with no note - and
then calls every GET route in the OpenAPI spec (so a route added later is swept too)
and the people write routes. Nothing may answer 5xx, and no answer stream may crash.
"""

import datetime as dt
import json
import re
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.schemas.derivations import (
	DERIVATION_STATUSES,
	KINDS,
	EventPayload,
	Extraction,
	IdeaPayload,
	TodoPayload,
)
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

TODAY = dt.datetime.now(dt.UTC).date()
LATER = (dt.datetime.now(dt.UTC) + dt.timedelta(days=3)).isoformat()


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		yield s


class Corpus:
	def __init__(
		self, client: AsyncClient, session: AsyncSession, store: FakeObjectStore
	) -> None:
		self.client, self.session, self.store = client, session, store
		self.captures: list[str] = []

	async def note(
		self,
		body: str,
		extraction: Extraction | None = None,
		people: list[MentionPayload] | None = None,
		*,
		spoken: str | None = None,
		admin: AsyncEngine | None = None,
	) -> str:
		if spoken is None:
			created = await self.client.post(
				"/v1/captures", json={"text": body, "client_token": f"t-{uuid.uuid4()}"}
			)
		else:
			assert admin is not None
			rec = (
				await self.client.post("/v1/recordings", json={"mime": "audio/m4a"})
			).json()
			async with admin.connect() as conn:
				key = await conn.scalar(
					text("SELECT storage_key FROM recordings WHERE id = :id"),
					{"id": rec["recording_id"]},
				)
			self.store.objects[str(key)] = b"audio"
			created = await self.client.post(
				"/v1/captures",
				json={
					"recording_id": rec["recording_id"],
					"client_token": f"t-{uuid.uuid4()}",
				},
			)
		assert created.status_code == 202, created.text
		capture_id = created.json()["id"]
		ctx = PipelineContext(
			session=self.session,
			transcriber=FakeTranscriber(text=spoken or ""),
			object_store=self.store,
			extractor=FakeExtractor(extraction or Extraction()),
			embedder=FakeEmbedder(),
			people=FakePeopleReader(PeopleExtraction(people=people or [])),
		)
		outcome = await process_capture(ctx, uuid.UUID(capture_id), SEED_USER_ID)
		assert outcome.status == "ready", outcome
		self.captures.append(capture_id)
		return str(capture_id)


async def _stream(client: AsyncClient, url: str) -> list[tuple[str, dict[str, Any]]]:
	events: list[tuple[str, dict[str, Any]]] = []
	name = None
	async with client.stream("GET", url) as response:
		assert response.status_code == 200
		async for line in response.aiter_lines():
			if line.startswith("event: "):
				name = line[len("event: ") :]
			elif line.startswith("data: ") and name is not None:
				events.append((name, json.loads(line[len("data: ") :])))
	return events


async def test_every_route_survives_every_kind_of_row(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
) -> None:
	store = FakeObjectStore()
	app.state.object_store = store
	corpus = Corpus(client, session, store)
	yesterday = (TODAY - dt.timedelta(days=1)).isoformat()

	# A linked person, with every item kind; this note is deleted below.
	doomed = await corpus.note(
		"lunch with Tunde Bakare yesterday; call him about pricing; dinner Friday",
		Extraction(
			todos=[TodoPayload(title="Call Tunde about pricing")],
			events=[EventPayload(title="Dinner", starts_at=LATER)],
			ideas=[IdeaPayload(title="Pricing", summary="Charge per seat.")],
		),
		[
			MentionPayload(
				name="Tunde",
				full_name="Tunde Bakare",
				contact="in_person",
				occurred_on=yesterday,
				context="pricing",
			)
		],
	)
	await corpus.note(
		"my cousin Tunde Ade is visiting",
		people=[MentionPayload(name="Tunde", full_name="Tunde Ade", hint="cousin")],
	)
	# Two Tundes: this one waits for "which one?".
	unsure = await corpus.note(
		"saw Tunde", people=[MentionPayload(name="Tunde", contact="in_person")]
	)
	# Auto-accept on: kept as 'accepted'.
	await client.patch("/v1/me", json={"auto_accept": True})
	await corpus.note(
		"texted Kemi, buy milk",
		Extraction(todos=[TodoPayload(title="Buy milk")]),
		[MentionPayload(name="Kemi", contact="message", occurred_on=str(TODAY))],
	)
	# A voice note where nothing was said.
	silent = await corpus.note("", spoken="   ", admin=admin_engine)
	# The first note goes; its items and its mention stay (ADR 0022).
	assert (await client.delete(f"/v1/captures/{doomed}")).status_code == 204

	people = {
		p["name"]: p["id"] for p in (await client.get("/v1/people")).json()["people"]
	}
	assert set(people) == {"Tunde Bakare", "Tunde Ade", "Kemi"}
	logged = await client.post(f"/v1/people/{people['Kemi']}/contact", json={})
	assert logged.status_code == 201

	conversation = (await client.post("/v1/conversations")).json()["id"]
	asked = (
		await client.post(
			f"/v1/conversations/{conversation}/messages",
			json={"text": "when did I last see Tunde, and what about Kemi?"},
		)
	).json()
	events = await _stream(client, asked["stream_url"])
	assert events[-1][0] == "done", events[-1]

	async with admin_engine.connect() as conn:
		derivation_ids = [
			str(r[0])
			for r in (
				await conn.execute(text("SELECT id FROM derivations ORDER BY id"))
			).all()
		]
	assert derivation_ids
	values: dict[str, list[str]] = {
		"capture_id": [*corpus.captures, doomed],
		"person_id": list(people.values()),
		"conversation_id": [conversation],
		"message_id": [asked["message_id"]],
		"derivation_id": derivation_ids,
	}
	queries: dict[str, list[str]] = {
		"/v1/derivations": [f"?status={s}" for s in DERIVATION_STATUSES]
		+ [f"?kind={k}" for k in KINDS]
		+ [""],
		"/v1/review/week": ["?weeks_ago=0", "?weeks_ago=1"],
	}

	swept = 0
	for path, ops in app.openapi()["paths"].items():
		if "get" not in ops or not path.startswith("/v1/"):
			continue
		names = re.findall(r"{(\w+)}", path)
		fills = [dict(zip(names, combo, strict=True)) for combo in _each(names, values)]
		for fill in fills:
			url = path.format(**fill)
			for query in queries.get(path, [""]):
				if path.endswith("/stream"):
					streamed = await _stream(client, url + query)
					assert all(
						e[1].get("code") != "internal_error" for e in streamed
					), url
				else:
					response = await client.get(url + query)
					assert response.status_code < 500, (url + query, response.text)
				swept += 1
	assert swept > 30

	# Search, over notes and over items whose note is gone.
	for q in ("Tunde", "pricing", "Kemi", "milk"):
		found = await client.post("/v1/search", json={"q": q, "limit": 20})
		assert found.status_code == 200, found.text

	# The people write routes, on the awkward rows.
	(waiting,) = (await client.get("/v1/people/unresolved")).json()
	assert waiting["capture_id"] == unsure
	for body in (
		{"person_id": people["Tunde Ade"]},
		{"status": "confirmed"},
		{"new_person": True},
		{"status": "rejected"},
	):
		response = await client.patch(f"/v1/mentions/{waiting['id']}", json=body)
		assert response.status_code < 500, (body, response.text)
	merged = await client.post(
		f"/v1/people/{people['Tunde Ade']}/merge", json={"into": people["Tunde Bakare"]}
	)
	assert merged.status_code == 200, merged.text
	for patch in (
		{"name": "Tunde B.", "aliases": ["TB"], "note": "work"},
		{"reach_out_every_days": None},
		{"snoozed_until": str(TODAY)},
	):
		response = await client.patch(
			f"/v1/people/{people['Tunde Bakare']}", json=patch
		)
		assert response.status_code == 200, (patch, response.text)
	# Editing an auto-accepted item, and retrying a note that is already fine.
	async with admin_engine.connect() as conn:
		accepted_id = await conn.scalar(
			text(
				"SELECT id FROM derivations WHERE status = 'accepted' AND kind = 'todo'"
			)
		)
	edited = await client.patch(
		f"/v1/derivations/{accepted_id}", json={"payload": {"title": "Buy oat milk"}}
	)
	assert edited.json()["status"] == "confirmed"
	assert (await client.post(f"/v1/captures/{silent}/retry")).status_code < 500
	exported = await client.get("/v1/me/export")
	assert exported.status_code == 200
	assert (await client.get("/v1/morning")).status_code == 200


def _each(names: list[str], values: dict[str, list[str]]) -> list[tuple[str, ...]]:
	"""Every value for a one-parameter path; the first value of each for more."""
	if not names:
		return [()]
	if len(names) == 1:
		return [(v,) for v in values[names[0]]]
	return [tuple(values[n][0] for n in names)]
