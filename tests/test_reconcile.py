"""A later note that answers a prayer or carries on an idea (ADR 0048): suggested,
never applied until the author says yes."""

import json
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import Extraction, IdeaPayload
from memo.schemas.reconcile import Reconciliation
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.reconcile import might_answer
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeReconciler,
	FakeTranscriber,
)

OFFER = "GOT THE OFFER from the Lagos team today, God is good"


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	reconciler: FakeReconciler,
	extraction: Extraction | None = None,
) -> uuid.UUID:
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
		reconciler=reconciler,
	)
	created = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	capture_id = uuid.UUID(created.json()["id"])
	assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"
	return capture_id


async def _prayer(client: AsyncClient, title: str) -> str:
	made = await client.post("/v1/faith/prayers", json={"title": title})
	assert made.status_code == 201, made.text
	prayer_id: str = made.json()["id"]
	return prayer_id


async def test_a_note_that_answers_a_prayer_asks_and_a_yes_answers_it(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	job = await _prayer(client, "the Lagos job")
	await _prayer(client, "Mum's surgery")
	reconciler = FakeReconciler({"OFFER": [("P2", "answers", "GOT THE OFFER")]})

	note = await _note(client, session, OFFER, reconciler)

	((seen_text, listed),) = reconciler.seen
	assert seen_text == OFFER
	# Newest first: the surgery was written after the job.
	assert [(i.label, i.title) for i in listed] == [
		("P1", "Mum's surgery"),
		("P2", "the Lagos job"),
	]
	(waiting,) = (await client.get("/v1/updates?status=pending")).json()
	assert waiting["relation"] == "answers" and waiting["quote"] == "GOT THE OFFER"
	assert waiting["target"]["id"] == job and waiting["capture_id"] == str(note)
	faith = (await client.get("/v1/faith")).json()
	assert faith["answered"] == [], "nothing changes before the yes"

	yes = await client.patch(f"/v1/updates/{waiting['id']}", json={"decision": "yes"})

	assert yes.status_code == 200, yes.text
	assert yes.json()["status"] == "confirmed"
	(answered,) = (await client.get("/v1/faith")).json()["answered"]
	assert answered["id"] == job and answered["answer"] == "GOT THE OFFER"
	assert (await client.get("/v1/updates?status=pending")).json() == []


async def test_no_faith_or_no_news_means_no_prayers_are_compared(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _prayer(client, "the Lagos job")  # Faith is off
	reconciler = FakeReconciler()
	await _note(client, session, OFFER, reconciler)
	assert reconciler.seen == [], "Faith off: prayers are never read"

	await client.patch("/v1/me", json={"faith_enabled": True})
	await _note(client, session, "praying for the Lagos job this week", reconciler)
	assert reconciler.seen == [], "no answer-ish word: no model call"


def test_the_answer_gate() -> None:
	for said in (OFFER, "Mum's surgery went well", "house sale is done!", "peace"):
		assert might_answer(said), said
	for said in ("praying for the Lagos job", "call Tunde about pricing", ""):
		assert not might_answer(said), said


async def test_a_note_that_carries_on_an_idea_joins_its_thread(
	client: AsyncClient, session: AsyncSession
) -> None:
	idea = Extraction(
		ideas=[
			IdeaPayload(
				title="Resurface one old note each morning",
				summary="One old note each morning instead of a feed",
			)
		]
	)
	await _note(
		client,
		session,
		"idea: resurface one old note each morning instead of a feed",
		FakeReconciler(),
		idea,
	)
	reconciler = FakeReconciler({"weekly": [("I1", "continues", "make it weekly")]})

	await _note(
		client,
		session,
		"the resurface one old note each morning idea: make it weekly instead",
		reconciler,
	)

	((_, listed),) = reconciler.seen
	assert [i.title for i in listed] == ["Resurface one old note each morning"]
	(waiting,) = (await client.get("/v1/updates?status=pending&kind=idea")).json()
	await client.patch(f"/v1/updates/{waiting['id']}", json={"decision": "yes"})
	(thread,) = (await client.get("/v1/updates?status=confirmed&kind=idea")).json()
	assert thread["target"]["title"] == "Resurface one old note each morning"
	assert thread["quote"] == "make it weekly"


async def test_a_no_is_kept_and_never_asked_again_for_that_note(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	await _prayer(client, "the Lagos job")
	reconciler = FakeReconciler({"OFFER": [("P1", "answers", "GOT THE OFFER")]})
	note = await _note(client, session, OFFER, reconciler)
	(waiting,) = (await client.get("/v1/updates?status=pending")).json()

	no = await client.patch(f"/v1/updates/{waiting['id']}", json={"decision": "no"})

	assert no.json()["status"] == "rejected"
	assert (await client.get("/v1/faith")).json()["answered"] == []
	# The note is read again (a retry, a new version of the stage): still no question.
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("DELETE FROM job_runs WHERE capture_id = :c AND stage = 'reconcile'"),
			{"c": note},
		)
		await conn.execute(
			text("UPDATE captures SET status = 'embedding' WHERE id = :c"), {"c": note}
		)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
		reconciler=reconciler,
	)
	assert (await process_capture(ctx, note, SEED_USER_ID)).status == "ready"
	assert (await client.get("/v1/updates?status=pending")).json() == []


async def test_answered_by_hand_first_settles_the_question(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	job = await _prayer(client, "the Lagos job")
	reconciler = FakeReconciler({"OFFER": [("P1", "answers", "GOT THE OFFER")]})
	await _note(client, session, OFFER, reconciler)
	(waiting,) = (await client.get("/v1/updates?status=pending")).json()

	await client.patch(
		f"/v1/faith/prayers/{job}", json={"answered": True, "answer": "Offer in March"}
	)

	assert (await client.get("/v1/updates?status=pending")).json() == []
	await client.patch(f"/v1/updates/{waiting['id']}", json={"decision": "yes"})
	(answered,) = (await client.get("/v1/faith")).json()["answered"]
	assert answered["answer"] == "Offer in March", "the author's own words stand"


async def test_deleting_the_prayer_takes_its_suggestions_with_it(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	job = await _prayer(client, "the Lagos job")
	await _note(
		client,
		session,
		OFFER,
		FakeReconciler({"OFFER": [("P1", "answers", "GOT THE OFFER")]}),
	)
	async with admin_engine.begin() as conn:
		await conn.execute(text("DELETE FROM derivations WHERE id = :id"), {"id": job})
		left = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE kind = 'update'")
		)
	assert left == 0


async def test_another_account_cannot_see_or_answer_a_suggestion(
	client: AsyncClient,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await client.patch("/v1/me", json={"faith_enabled": True})
	await _prayer(client, "the Lagos job")
	await _note(
		client,
		session,
		OFFER,
		FakeReconciler({"OFFER": [("P1", "answers", "GOT THE OFFER")]}),
	)
	(waiting,) = (await client.get("/v1/updates?status=pending")).json()

	as_user(second_user)

	assert (await client.get("/v1/updates")).json() == []
	refused = await client.patch(
		f"/v1/updates/{waiting['id']}", json={"decision": "yes"}
	)
	assert refused.status_code == 404


def _raw(*changes: tuple[str, str, str]) -> str:
	return json.dumps(
		{
			"changes": [
				{"candidate": c, "relation": r, "quote": q, "confidence": 1}
				for c, r, q in changes
			]
		}
	)


@pytest.mark.parametrize(
	("raw", "why"),
	[
		(_raw(("P3", "answers", "GOT THE OFFER")), "not one of"),
		(_raw(("I1", "answers", "GOT THE OFFER")), "can only be"),
		(_raw(("P1", "answers", "received an offer")), "copied exactly"),
		(
			_raw(("P1", "answers", "GOT THE OFFER"), ("P1", "answers", "God is good")),
			"twice",
		),
	],
)
def test_validation_checks_the_answer_against_the_note(raw: str, why: str) -> None:
	context = {"labels": {"P1", "I1"}, "entry": OFFER}
	with pytest.raises(ValueError, match=why):
		Reconciliation.model_validate_json(raw, context=context)
	# Case and spacing don't make a quote a different quote.
	ok = _raw(("P1", "answers", "got the  offer from the lagos team"))
	assert Reconciliation.model_validate_json(ok, context=context).changes
