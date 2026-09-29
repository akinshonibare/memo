"""What Ask reads from records before it searches (ADR 0044): the days a question is
about, the items dated in them, and the last contact with anyone it names."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.models import Derivation, Person
from memo.schemas.derivations import EventPayload, Extraction, TodoPayload
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.ask import _item_days
from memo.services.people import named_in
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retrieval import search
from memo.services.timewindow import Window
from tests.fakes import (
	FakeAnswerProvider,
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeRewriteProvider,
	FakeTranscriber,
)
from tests.test_ask_api import _ask, _events

UTC = ZoneInfo("UTC")


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
	extraction: Extraction | None = None,
	people: PeopleExtraction | None = None,
) -> uuid.UUID:
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(people),
	)
	created = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	capture_id = uuid.UUID(created.json()["id"])
	assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"
	return capture_id


async def _written(engine: AsyncEngine, capture_id: uuid.UUID, days_ago: int) -> None:
	"""Move a note (and its chunks) back in time, as if written `days_ago`."""
	async with engine.begin() as conn:
		for table, column in (("captures", "id"), ("chunks", "capture_id")):
			await conn.execute(
				text(
					f"UPDATE {table} "
					"SET created_at = now() - make_interval(days => :d) "
					f"WHERE {column} = :c"
				),
				{"d": days_ago, "c": capture_id},
			)


async def test_whats_on_tomorrow_finds_an_old_note_with_no_words_in_common(
	client: AsyncClient,
	session: AsyncSession,
	app: FastAPI,
	admin_engine: AsyncEngine,
) -> None:
	noon = dt.datetime.now(dt.UTC).replace(hour=12, minute=0, second=0, microsecond=0)
	tomorrow = noon + dt.timedelta(days=1)
	plumber = await _note(
		client,
		session,
		"the plumber is coming round to look at the boiler",
		extraction=Extraction(
			events=[EventPayload(title="Plumber", starts_at=tomorrow)]
		),
	)
	await _written(admin_engine, plumber, 20)
	for body in ("felt anxious about work all day", "what's on my mind: pricing"):
		await _note(client, session, body)
	answers = FakeAnswerProvider()
	app.state.answer_provider = answers

	accepted = await _ask(client, "what's on tomorrow?")
	events = await _events(client, accepted["stream_url"])

	citations = events[0][1]["citations"]
	assert citations[0]["capture_id"] == str(plumber), "the dated item comes first"
	prompt = str(answers.last_messages[-1]["content"])
	assert f"The question is about: {tomorrow.strftime('%A %-d %B %Y')}." in prompt
	assert "event: Plumber" in prompt


async def test_a_window_keeps_to_notes_written_in_it(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	old = await _note(client, session, "long walk by the river, felt clear")
	new = await _note(client, session, "short walk by the river, felt tired")
	await _written(admin_engine, old, 10)
	now = dt.datetime.now(dt.UTC)

	windowed = await search(
		session,
		user_id=SEED_USER_ID,
		query="walk by the river",
		embedder=FakeEmbedder(),
		window=(now - dt.timedelta(days=2), now + dt.timedelta(minutes=1)),
	)
	everything = await search(
		session,
		user_id=SEED_USER_ID,
		query="walk by the river",
		embedder=FakeEmbedder(),
	)

	assert [h.capture_id for h in windowed.hits] == [new]
	assert {h.capture_id for h in everything.hits} == {old, new}


async def test_when_did_i_last_see_reads_the_latest_contact_not_the_best_match(
	client: AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
	def kemi(contact: str, day: dt.date | None) -> PeopleExtraction:
		return PeopleExtraction(
			people=[MentionPayload(name="Kemi", contact=contact, occurred_on=day)]
		)

	await _note(
		client,
		session,
		"saw Kemi, when did we last see each other before that",
		people=kemi("in_person", dt.date(2026, 9, 1)),
	)
	called = await _note(
		client,
		session,
		"called her about the flat",
		people=kemi("call", dt.date(2026, 9, 20)),
	)
	await _note(
		client, session, "Kemi's sister is moving to Leeds", people=kemi("none", None)
	)
	await _note(
		client,
		session,
		"Bola might join the running club",
		people=PeopleExtraction(people=[MentionPayload(name="Bola")]),
	)
	answers = FakeAnswerProvider()
	app.state.answer_provider = answers

	accepted = await _ask(client, "when did I last see Kemi, and Bola?")
	events = await _events(client, accepted["stream_url"])

	citations = events[0][1]["citations"]
	assert citations[0]["capture_id"] == str(called)
	prompt = str(answers.last_messages[-1]["content"])
	assert "People named in the question:" in prompt
	assert "- Kemi: last in touch Sun 20 Sep 2026, spoke on the phone [1]" in prompt
	assert "- Bola: no contact recorded; mentioned in 1 note(s)" in prompt


async def test_a_follow_up_finds_the_person_the_rewrite_names(
	client: AsyncClient, session: AsyncSession, app: FastAPI
) -> None:
	await _note(
		client,
		session,
		"coffee with Kemi",
		people=PeopleExtraction(
			people=[
				MentionPayload(
					name="Kemi", contact="in_person", occurred_on=dt.date(2026, 9, 12)
				)
			]
		),
	)
	rewriter: FakeRewriteProvider = app.state.rewrite_provider
	rewriter.rewritten = "when did I last see Kemi"
	answers = FakeAnswerProvider()
	app.state.answer_provider = answers
	first = await _ask(client, "what's Kemi up to?")
	await _events(client, first["stream_url"])

	follow_up = await _ask(
		client, "and when did I last see her?", first["conversation_id"]
	)
	await _events(client, follow_up["stream_url"])

	prompt = str(answers.last_messages[-1]["content"])
	assert "- Kemi: last in touch Sat 12 Sep 2026, in touch in person [1]" in prompt


def _person(name: str, *aliases: str) -> Person:
	return Person(id=uuid.uuid4(), name=name, aliases=list(aliases))


def test_named_in_prefers_a_full_name_but_keeps_everyone_named() -> None:
	bakare = _person("Tunde Bakare")
	ade = _person("Tunde Ade")
	mum = _person("Folake Shonibare", "Mum")
	people = [bakare, ade, mum]

	assert named_in("when did I last see Tunde Ade?", people) == [ade]
	assert named_in("when did I last see Tunde?", people) == [bakare, ade]
	assert named_in("did I call mum or Tunde Ade", people) == [ade, mum]
	assert named_in("what about Tunde's idea", people) == [bakare, ade]
	assert named_in("what's on this week", people) == []


def test_an_item_belongs_to_its_date_and_the_day_it_was_done() -> None:
	done = Derivation(
		kind="todo",
		payload=TodoPayload(title="Renew", due=dt.date(2026, 9, 1)).model_dump(
			mode="json"
		),
		completed_at=dt.datetime(2026, 9, 29, 18, tzinfo=dt.UTC),
	)
	broken = Derivation(kind="event", payload={"title": "x", "starts_at": "soon"})
	assert _item_days(done, UTC) == [dt.date(2026, 9, 1), dt.date(2026, 9, 29)]
	assert _item_days(broken, UTC) == []
	week = Window(dt.date(2026, 9, 28), dt.date(2026, 10, 4), "this week")
	assert any(week.contains(d) for d in _item_days(done, UTC))
