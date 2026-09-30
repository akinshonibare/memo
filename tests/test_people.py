"""People (ADR 0029): reading, resolving, the "which one?" question, and reminders."""

import asyncio
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
from memo.models import Capture, Person
from memo.prompts.answer_v2 import extracted_line
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.extraction import ExtractionFailed
from memo.services.people import (
	PeopleReader,
	apply_mentions,
	compatible,
	is_due,
	normalize,
	resolve,
	similar,
	unread_notes,
	with_alias,
)
from memo.services.pipeline import (
	PipelineContext,
	process_capture,
	read_people_in_old_note,
)
from memo.services.retry import TerminalError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)
from tests.test_extraction_service import ScriptedProvider

TODAY = dt.datetime.now(dt.UTC).date()


def _mention(name: str, **kw: object) -> MentionPayload:
	return MentionPayload.model_validate({"name": name, **kw})


def _person(name: str, *aliases: str) -> Person:
	return Person(id=uuid.uuid4(), user_id=SEED_USER_ID, name=name, aliases=[*aliases])


# --- matching, without a database ---------------------------------------------------


def test_normalize_ignores_case_accents_punctuation_and_my() -> None:
	assert normalize("Túndé") == "tunde"
	assert normalize("  my Mum! ") == "mum"
	assert normalize("Dr. Okafor") == "dr okafor"


def test_compatible_is_one_name_inside_the_other() -> None:
	assert compatible("Tunde", "Tunde Bakare")
	assert compatible("Bakare", "Tunde Bakare")
	assert compatible("TUNDE bakare", "Tunde Bakare")
	assert not compatible("Tunde Ade", "Tunde Bakare")
	assert not compatible("Mum", "Folake")


def test_similar_catches_misspellings_but_not_other_surnames() -> None:
	assert similar("Tunday", "Tunde")
	assert similar("Ada", "Adaeze")
	assert similar("Tunday Bakare", "Tunde Bakare")
	# A different surname is a different person, not a typo.
	assert not similar("Tunde Ade", "Tunde Bakare")
	assert not similar("Tobi", "Kemi")
	# A surname one letter off someone's first name is not them (found seeding:
	# "Tunde Ade" was asked about as "Ada").
	assert not similar("Tunde Ade", "Ada")


def test_one_fitting_person_links() -> None:
	tunde = _person("Tunde Bakare")
	result = resolve(_mention("Tunde"), [tunde, _person("Kemi")])
	assert result.outcome == "link" and result.person_id == tunde.id


def test_two_fitting_people_ask_instead_of_guessing() -> None:
	a, b = _person("Tunde Bakare"), _person("Tunde Ade")
	result = resolve(_mention("Tunde"), [a, b])
	assert result.outcome == "ask"
	assert set(result.candidates) == {a.id, b.id}


def test_a_fuller_name_settles_it() -> None:
	a, b = _person("Tunde Bakare"), _person("Tunde Ade")
	result = resolve(_mention("Tunde", full_name="Tunde Ade"), [a, b])
	assert result.outcome == "link" and result.person_id == b.id


def test_an_alias_links() -> None:
	mum = _person("Folake Shonibare", "Mum")
	result = resolve(_mention("my mum"), [mum])
	assert result.outcome == "link" and result.person_id == mum.id


def test_a_near_spelling_is_a_question_never_a_link() -> None:
	tunde = _person("Tunde Bakare")
	result = resolve(_mention("Tunday"), [tunde])
	assert result.outcome == "ask" and result.candidates == [tunde.id]


def test_nobody_fitting_is_someone_new() -> None:
	assert resolve(_mention("Kemi"), [_person("Tunde")]).outcome == "new"
	assert resolve(_mention("Kemi"), []).outcome == "new"


def test_aliases_are_learned_only_when_they_teach_something() -> None:
	folake = _person("Folake Shonibare")
	assert with_alias(folake, "Mum") == ["Mum"]
	assert with_alias(_person("Tunde Bakare"), "Tunde") == []
	assert with_alias(_person("Folake", "Mum"), "mum") == ["Mum"]


def test_due_needs_a_rhythm_a_past_contact_and_no_snooze() -> None:
	p = _person("Tunde")
	p.reach_out_every_days = 30
	month_ago = TODAY - dt.timedelta(days=30)
	assert is_due(p, month_ago, TODAY)
	assert not is_due(p, month_ago + dt.timedelta(days=1), TODAY)
	# Only ever mentioned, never in touch: the clock never started.
	assert not is_due(p, None, TODAY)
	p.snoozed_until = TODAY + dt.timedelta(days=1)
	assert not is_due(p, month_ago, TODAY)
	p.snoozed_until = TODAY
	assert is_due(p, month_ago, TODAY)
	p.reach_out_every_days = None
	assert not is_due(p, month_ago, TODAY)


def test_contact_after_the_note_was_written_is_rejected() -> None:
	future = {"name": "Tunde", "contact": "call", "occurred_on": "2026-10-02"}
	with pytest.raises(ValidationError, match="after the note was written"):
		MentionPayload.model_validate(
			future, context={"captured_on": dt.date(2026, 9, 28)}
		)
	mentioned = MentionPayload.model_validate(
		{"name": "Tunde", "contact": "none", "occurred_on": "2026-09-20"}
	)
	assert mentioned.occurred_on is None


async def test_the_reader_repairs_a_future_contact_date() -> None:
	now = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)
	bad = PeopleExtraction(
		people=[_mention("Tunde", contact="call", occurred_on="2026-10-02")]
	).model_dump_json()
	good = PeopleExtraction(
		people=[_mention("Tunde", contact="none")]
	).model_dump_json()
	provider = ScriptedProvider([bad, good])
	result = await PeopleReader(provider).read(
		"calling Tunde on Friday", captured_at=now, timezone="UTC", author="Akin"
	)
	assert result.extraction.people[0].contact == "none"
	assert [c.stage for c in result.calls] == ["people", "people"]
	assert "The author is Akin." in str(provider.requests[0][0]["content"])
	assert "after the note was written" in str(provider.requests[1][2]["content"])

	provider = ScriptedProvider([bad, bad])
	with pytest.raises(ExtractionFailed):
		await PeopleReader(provider).read(
			"x", captured_at=now, timezone="UTC", author=None
		)


def test_the_answer_prompt_says_whether_it_was_contact() -> None:
	zone = dt.UTC
	contact = extracted_line(
		"mention",
		{"name": "Mum", "contact": "call", "occurred_on": "2026-09-20"},
		"confirmed",
		zone,  # type: ignore[arg-type]
		person="Folake Shonibare",
	)
	assert contact == (
		"person: Folake Shonibare · spoke on the phone on Sun 20 Sep 2026 (confirmed)"
	)
	mention = extracted_line(
		"mention",
		{"name": "Kemi", "contact": "none", "context": "got the job"},
		"pending_review",
		zone,  # type: ignore[arg-type]
	)
	assert (
		mention == "person: Kemi · mentioned, not in touch · got the job (unreviewed)"
	)


# --- through the pipeline and the API ------------------------------------------------


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


def _read(*people: MentionPayload) -> PeopleExtraction:
	return PeopleExtraction(people=list(people))


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	reader: FakePeopleReader,
	user_id: uuid.UUID = SEED_USER_ID,
) -> uuid.UUID:
	created = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	assert created.status_code == 202, created.text
	capture_id = uuid.UUID(created.json()["id"])
	outcome = await process_capture(_ctx(session, reader), capture_id, user_id)
	assert outcome.status == "ready"
	return capture_id


async def test_a_note_names_people_and_memo_meets_them(
	client: AsyncClient, session: AsyncSession
) -> None:
	yesterday = TODAY - dt.timedelta(days=1)
	reader = FakePeopleReader(
		_read(
			_mention(
				"Tunde",
				full_name="Tunde Bakare",
				hint="from work",
				contact="in_person",
				occurred_on=yesterday.isoformat(),
				context="started at Flutterwave",
			),
			_mention("Kemi", contact="none", context="got the job, says Ada"),
		)
	)
	capture_id = await _note(
		client, session, "lunch with Tunde yesterday; Kemi got the job", reader
	)

	note = (await client.get(f"/v1/captures/{capture_id}")).json()
	# Mentions have their own field: an app built before people never sees them.
	assert note["derivations"] == []
	assert [(m["name"], m["contact"]) for m in note["people"]] == [
		("Tunde", "in_person"),
		("Kemi", "none"),
	]
	assert note["people"][0]["person"]["name"] == "Tunde Bakare"
	assert note["people"][0]["person"]["note"] == "from work"
	assert (await client.get("/v1/derivations")).json()["items"] == []

	listed = (await client.get("/v1/people")).json()
	by_name = {p["name"]: p for p in listed["people"]}
	assert set(by_name) == {"Tunde Bakare", "Kemi"}
	assert by_name["Tunde Bakare"]["last_contact_on"] == yesterday.isoformat()
	assert by_name["Tunde Bakare"]["aliases"] == []
	# Heard about, never in touch: no last contact, so never a reminder.
	assert by_name["Kemi"]["last_contact_on"] is None
	assert by_name["Kemi"]["reach_out_every_days"] == 30
	assert listed["unresolved"] == 0

	# The next note that says "Tunde" is the same Tunde.
	again = FakePeopleReader(_read(_mention("Tunde", contact="call")))
	second = await _note(client, session, "called Tunde", again)
	linked = (await client.get(f"/v1/captures/{second}")).json()["people"][0]
	assert linked["person"]["id"] == by_name["Tunde Bakare"]["id"]
	# No date given for the call: it happened when the note was written.
	assert linked["occurred_on"] == TODAY.isoformat()


async def test_a_name_that_fits_two_people_waits_for_the_user(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(
		client,
		session,
		"two Tundes",
		FakePeopleReader(
			_read(
				_mention("Tunde", full_name="Tunde Bakare", hint="work"),
				_mention("Tunde", full_name="Tunde Ade", hint="cousin"),
			)
		),
	)
	capture_id = await _note(
		client,
		session,
		"saw Tunde",
		FakePeopleReader(_read(_mention("Tunde", contact="in_person"))),
	)

	waiting = (await client.get("/v1/people/unresolved")).json()
	assert len(waiting) == 1
	assert waiting[0]["person"] is None
	assert waiting[0]["excerpt"] == "saw Tunde"
	candidates = {c["name"]: c for c in waiting[0]["candidates"]}
	assert set(candidates) == {"Tunde Bakare", "Tunde Ade"}
	assert (await client.get("/v1/people")).json()["unresolved"] == 1
	# Not in touch with anyone yet: an unlinked contact moves nobody's date.
	people = (await client.get("/v1/people")).json()["people"]
	assert all(p["last_contact_on"] is None for p in people)

	answer = await client.patch(
		f"/v1/mentions/{waiting[0]['id']}",
		json={"person_id": candidates["Tunde Ade"]["id"]},
	)
	assert answer.status_code == 200, answer.text
	assert answer.json()["status"] == "confirmed"
	assert answer.json()["person"]["name"] == "Tunde Ade"
	assert (await client.get("/v1/people/unresolved")).json() == []
	detail = (await client.get(f"/v1/people/{candidates['Tunde Ade']['id']}")).json()
	assert detail["last_contact_on"] == TODAY.isoformat()
	assert [m["capture_id"] for m in detail["mentions"]][0] == str(capture_id)


async def test_answering_teaches_memo_a_new_name(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(
		client,
		session,
		"about Folake",
		FakePeopleReader(_read(_mention("Folake", full_name="Folake Shonibare"))),
	)
	await _note(
		client,
		session,
		"called mum",
		FakePeopleReader(_read(_mention("Mum", contact="call"))),
	)
	people = {p["name"]: p for p in (await client.get("/v1/people")).json()["people"]}
	# "Mum" matched nobody, so memo met a new person called Mum.
	assert set(people) == {"Folake Shonibare", "Mum"}

	# Wrong: Mum is Folake. Merge, and "Mum" becomes one of her names.
	merged = await client.post(
		f"/v1/people/{people['Mum']['id']}/merge",
		json={"into": people["Folake Shonibare"]["id"]},
	)
	assert merged.status_code == 200, merged.text
	assert merged.json()["aliases"] == ["Mum"]
	assert merged.json()["mention_count"] == 2
	assert merged.json()["last_contact_on"] == TODAY.isoformat()
	assert (await client.get(f"/v1/people/{people['Mum']['id']}")).status_code == 404

	# So the next "my mum" links on its own.
	third = await _note(
		client,
		session,
		"texted my mum",
		FakePeopleReader(_read(_mention("my mum", contact="message"))),
	)
	linked = (await client.get(f"/v1/captures/{third}")).json()["people"][0]
	assert linked["person"]["name"] == "Folake Shonibare"


async def test_someone_new_and_not_a_person(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde"))))
	capture_id = await _note(
		client, session, "Tunday", FakePeopleReader(_read(_mention("Tunday")))
	)
	(waiting,) = (await client.get("/v1/people/unresolved")).json()
	assert [c["name"] for c in waiting["candidates"]] == ["Tunde"]

	made = await client.patch(
		f"/v1/mentions/{waiting['id']}", json={"new_person": True}
	)
	assert made.json()["person"]["name"] == "Tunday"
	assert len((await client.get("/v1/people")).json()["people"]) == 2

	# "Not a person": rejecting the only mention of someone removes them too.
	rejected = await client.patch(
		f"/v1/mentions/{waiting['id']}", json={"status": "rejected"}
	)
	assert rejected.json()["status"] == "rejected"
	names = [p["name"] for p in (await client.get("/v1/people")).json()["people"]]
	assert names == ["Tunde"]
	note = (await client.get(f"/v1/captures/{capture_id}")).json()
	assert note["people"] == []

	bad = await client.patch(
		f"/v1/mentions/{waiting['id']}", json={"person_id": None, "status": None}
	)
	assert bad.status_code == 422


async def test_mentions_are_not_items(
	app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
	capture_id = await _note(
		client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde")))
	)
	async with app.state.session_factory() as s, s.begin():
		await bind_user(s, SEED_USER_ID)
		mention_id = await s.scalar(
			text("SELECT id FROM derivations WHERE capture_id = :c"), {"c": capture_id}
		)
	refused = await client.patch(
		f"/v1/derivations/{mention_id}", json={"status": "confirmed"}
	)
	assert refused.status_code == 422
	assert (await client.get("/v1/derivations?kind=mention")).status_code == 422
	add = await client.post(
		"/v1/derivations", json={"kind": "mention", "payload": {"name": "x"}}
	)
	assert add.status_code == 422


async def test_rereading_a_note_does_not_duplicate_people(
	app: FastAPI, client: AsyncClient, session: AsyncSession
) -> None:
	reader = FakePeopleReader(_read(_mention("Kemi"), _mention("Ada")))
	capture_id = await _note(client, session, "Kemi and Ada", reader)
	async with app.state.session_factory() as s, s.begin():
		await bind_user(s, SEED_USER_ID)
		capture = await s.get(Capture, capture_id)
		assert capture is not None
		await apply_mentions(
			s,
			capture,
			reader.extraction,
			version=reader.version,
			timezone="UTC",
		)
	people = (await client.get("/v1/people")).json()["people"]
	assert sorted(p["name"] for p in people) == ["Ada", "Kemi"]
	assert all(p["mention_count"] == 1 for p in people)


async def test_one_person_twice_in_a_note_is_one_mention(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde"))))
	capture_id = await _note(
		client,
		session,
		"Tunde twice",
		FakePeopleReader(
			_read(
				_mention("Tunde", contact="none"),
				_mention("Tunde Bakare", contact="call"),
			)
		),
	)
	(only,) = (await client.get(f"/v1/captures/{capture_id}")).json()["people"]
	assert only["contact"] == "call"


async def test_two_notes_at_once_make_one_new_person(
	app: FastAPI, client: AsyncClient
) -> None:
	"""The per-user advisory lock: resolution is check-then-insert, so without it two
	notes naming a new "Kemi" read at the same moment would each create one."""
	ids = []
	for n in range(2):
		created = await client.post(
			"/v1/captures", json={"text": f"Kemi {n}", "client_token": f"tok-kemi-{n}"}
		)
		ids.append(uuid.UUID(created.json()["id"]))
	extraction = _read(_mention("Kemi"))

	async def apply(capture_id: uuid.UUID, hold: asyncio.Event | None) -> None:
		async with app.state.session_factory() as s, s.begin():
			await bind_user(s, SEED_USER_ID)
			capture = await s.get(Capture, capture_id)
			assert capture is not None
			await apply_mentions(
				s, capture, extraction, version="people-v1/fake", timezone="UTC"
			)
			if hold is not None:
				hold.set()
				await asyncio.sleep(0.3)  # still holding the lock, not committed

	held = asyncio.Event()
	first = asyncio.create_task(apply(ids[0], held))
	await held.wait()
	await apply(ids[1], None)  # blocks on the lock until the first commits
	await first
	people = (await client.get("/v1/people")).json()["people"]
	assert [p["name"] for p in people] == ["Kemi"]
	assert people[0]["mention_count"] == 2


async def test_reach_out_today(client: AsyncClient, session: AsyncSession) -> None:
	long_ago = TODAY - dt.timedelta(days=40)
	recently = TODAY - dt.timedelta(days=3)
	await _note(
		client,
		session,
		"catching up",
		FakePeopleReader(
			_read(
				_mention(
					"Tunde",
					contact="call",
					occurred_on=long_ago.isoformat(),
					context="waiting on visa news",
				),
				_mention("Ada", contact="in_person", occurred_on=recently.isoformat()),
				_mention("Kemi", contact="none"),
			)
		),
	)
	people = {p["name"]: p for p in (await client.get("/v1/people")).json()["people"]}
	assert people["Tunde"]["due"] and not people["Ada"]["due"]
	assert not people["Kemi"]["due"]

	morning = (await client.get("/v1/morning")).json()
	(tunde,) = morning["reach_out"]
	assert tunde["name"] == "Tunde"
	assert tunde["last_contact_on"] == long_ago.isoformat()
	assert tunde["every_days"] == 30
	assert tunde["latest"] == "waiting on visa news"

	# "Not now": gone until the snooze ends.
	snooze = (TODAY + dt.timedelta(days=7)).isoformat()
	patched = await client.patch(
		f"/v1/people/{tunde['person_id']}", json={"snoozed_until": snooze}
	)
	assert patched.json()["due"] is False
	assert (await client.get("/v1/morning")).json()["reach_out"] == []
	await client.patch(f"/v1/people/{tunde['person_id']}", json={"snoozed_until": None})
	assert len((await client.get("/v1/morning")).json()["reach_out"]) == 1

	# "Spoke to them": logged, off the list, and the snooze is cleared.
	spoke = await client.post(f"/v1/people/{tunde['person_id']}/contact", json={})
	assert spoke.status_code == 201, spoke.text
	assert spoke.json()["last_contact_on"] == TODAY.isoformat()
	assert (await client.get("/v1/morning")).json()["reach_out"] == []
	future = (TODAY + dt.timedelta(days=1)).isoformat()
	refused = await client.post(
		f"/v1/people/{tunde['person_id']}/contact", json={"occurred_on": future}
	)
	assert refused.status_code == 422

	# A rhythm is per person, and null turns it off.
	ada = people["Ada"]["id"]
	weekly = await client.patch(f"/v1/people/{ada}", json={"reach_out_every_days": 2})
	assert weekly.json()["due"] is True
	off = await client.patch(f"/v1/people/{ada}", json={"reach_out_every_days": None})
	assert off.json()["due"] is False and off.json()["reach_out_every_days"] is None


async def test_undoing_a_mistaken_spoke_to_them(
	client: AsyncClient, session: AsyncSession
) -> None:
	"""The app's undo: reject the contact that "Spoke to them" logged, and last
	contact goes back to what the notes say."""
	before = TODAY - dt.timedelta(days=40)
	await _note(
		client,
		session,
		"called Tunde",
		FakePeopleReader(
			_read(_mention("Tunde", contact="call", occurred_on=before.isoformat()))
		),
	)
	(tunde,) = (await client.get("/v1/people")).json()["people"]
	await client.post(f"/v1/people/{tunde['id']}/contact", json={})
	page = (await client.get(f"/v1/people/{tunde['id']}")).json()
	assert page["last_contact_on"] == TODAY.isoformat()
	logged = next(m for m in page["mentions"] if m["status"] == "user_created")
	assert logged["capture_id"] is None

	undone = await client.patch(
		f"/v1/mentions/{logged['id']}", json={"status": "rejected"}
	)

	assert undone.status_code == 200, undone.text
	page = (await client.get(f"/v1/people/{tunde['id']}")).json()
	assert page["last_contact_on"] == before.isoformat()
	assert page["due"] is True
	assert [m["status"] for m in page["mentions"]] == ["pending_review"]


async def test_today_shows_at_most_three(
	client: AsyncClient, session: AsyncSession
) -> None:
	names = ["Ada", "Bola", "Chidi", "Dayo", "Emeka"]
	await _note(
		client,
		session,
		"a big reunion",
		FakePeopleReader(
			_read(
				*(
					_mention(
						n,
						contact="in_person",
						occurred_on=(TODAY - dt.timedelta(days=31 + i)).isoformat(),
					)
					for i, n in enumerate(names)
				)
			)
		),
	)
	shown = (await client.get("/v1/morning")).json()["reach_out"]
	# Longest past their rhythm first.
	assert [r["name"] for r in shown] == ["Emeka", "Dayo", "Chidi"]


async def test_renaming_keeps_the_old_name_as_an_alias(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, "mum", FakePeopleReader(_read(_mention("Mum"))))
	(mum,) = (await client.get("/v1/people")).json()["people"]
	renamed = await client.patch(
		f"/v1/people/{mum['id']}", json={"name": "Folake Shonibare", "note": "mum"}
	)
	assert renamed.json()["name"] == "Folake Shonibare"
	assert renamed.json()["aliases"] == ["Mum"]
	assert renamed.json()["note"] == "mum"
	blank = await client.patch(f"/v1/people/{mum['id']}", json={"name": "  "})
	assert blank.status_code == 422


async def test_a_person_page_has_the_notes_and_their_open_items(
	client: AsyncClient, session: AsyncSession
) -> None:
	from memo.schemas.derivations import Extraction, TodoPayload

	created = await client.post(
		"/v1/captures",
		json={"text": "call Tunde about pricing", "client_token": "tok-tunde-todo"},
	)
	capture_id = uuid.UUID(created.json()["id"])
	ctx = _ctx(session, FakePeopleReader(_read(_mention("Tunde"))))
	ctx.extractor = FakeExtractor(
		Extraction(todos=[TodoPayload(title="Call Tunde about pricing")])
	)
	await process_capture(ctx, capture_id, SEED_USER_ID)
	(tunde,) = (await client.get("/v1/people")).json()["people"]
	page = (await client.get(f"/v1/people/{tunde['id']}")).json()
	assert [t["payload"]["title"] for t in page["open_todos"]] == [
		"Call Tunde about pricing"
	]
	assert page["mentions"][0]["excerpt"] == "call Tunde about pricing"


async def test_people_are_per_user(
	client: AsyncClient,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	capture_id = await _note(
		client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde")))
	)
	(mine,) = (await client.get("/v1/people")).json()["people"]
	(mention,) = (await client.get(f"/v1/captures/{capture_id}")).json()["people"]

	as_user(second_user)
	assert (await client.get("/v1/people")).json()["people"] == []
	assert (await client.get(f"/v1/people/{mine['id']}")).status_code == 404
	assert (
		await client.patch(f"/v1/mentions/{mention['id']}", json={"status": "rejected"})
	).status_code == 404
	assert (
		await client.post(f"/v1/people/{mine['id']}/contact", json={})
	).status_code == 404


async def test_a_mention_cannot_point_at_another_users_person(
	app: FastAPI, client: AsyncClient, session: AsyncSession, second_user: uuid.UUID
) -> None:
	"""The composite foreign key, not the route, is what guarantees it."""
	capture_id = await _note(
		client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde")))
	)
	theirs = uuid.uuid4()
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		await s.execute(
			text("INSERT INTO people (id, user_id, name) VALUES (:id, :u, 'Kemi')"),
			{"id": theirs, "u": second_user},
		)
	with pytest.raises(Exception, match="fk_derivations_person_same_user"):
		async with app.state.session_factory() as s, s.begin():
			await bind_system(s)
			await s.execute(
				text("UPDATE derivations SET person_id = :p WHERE capture_id = :c"),
				{"p": theirs, "c": capture_id},
			)


async def test_old_notes_are_read_by_the_backlog(
	app: FastAPI,
	client: AsyncClient,
	session: AsyncSession,
	admin_engine: AsyncEngine,
) -> None:
	capture_id = await _note(
		client, session, "lunch with Kemi", FakePeopleReader(_read())
	)
	# As if the note were processed before the people stage existed.
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("DELETE FROM job_runs WHERE capture_id = :c AND stage = 'people'"),
			{"c": capture_id},
		)
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		assert await unread_notes(s, limit=10) == [(capture_id, SEED_USER_ID)]
	assert (await client.get("/v1/people")).json()["unread_notes"] == 1

	failing = FakePeopleReader(fail_with=TerminalError("model refused"))
	assert (
		await read_people_in_old_note(_ctx(session, failing), capture_id, SEED_USER_ID)
		== "failed"
	)
	note = (await client.get(f"/v1/captures/{capture_id}")).json()
	assert note["status"] == "ready"  # the note itself is untouched

	reader = FakePeopleReader(
		_read(_mention("Kemi", contact="in_person", context="lunch"))
	)
	assert (
		await read_people_in_old_note(_ctx(session, reader), capture_id, SEED_USER_ID)
		== "read"
	)
	assert (
		await read_people_in_old_note(_ctx(session, reader), capture_id, SEED_USER_ID)
		== "done"
	)
	assert reader.calls == 1
	async with admin_engine.connect() as conn:
		chunk = await conn.scalar(
			text(
				"SELECT k.text FROM chunks k "
				"JOIN derivations d ON d.id = k.derivation_id "
				"WHERE d.kind = 'mention' AND k.superseded_at IS NULL"
			)
		)
		run = (
			await conn.execute(
				text(
					"SELECT status, attempt FROM job_runs "
					"WHERE capture_id = :c AND stage = 'people'"
				),
				{"c": capture_id},
			)
		).one()
	assert chunk == f"person: Kemi. met in person on {TODAY.isoformat()}. lunch"
	assert tuple(run) == ("succeeded", 2)
	assert (await client.get("/v1/people")).json()["unread_notes"] == 0


async def test_export_and_delete_everything_cover_people(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _note(client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde"))))
	exported = (await client.get("/v1/me/export")).json()
	assert [p["name"] for p in exported["people"]] == ["Tunde"]
	mention = exported["captures"][0]["derivations"][0]
	assert mention["kind"] == "mention"
	assert mention["person_id"] == exported["people"][0]["id"]

	deleted = await client.delete("/v1/me/data", params={"confirm": str(SEED_USER_ID)})
	assert deleted.json()["deleted"]["people"] == 1
	async with admin_engine.connect() as conn:
		assert await conn.scalar(text("SELECT count(*) FROM people")) == 0


async def test_deleting_a_person_forgets_them_and_keeps_the_notes(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	reader = FakePeopleReader(_read(_mention("Tunde", contact="in_person")))
	note = await _note(client, session, "coffee with Tunde", reader)
	(tunde,) = (await client.get("/v1/people")).json()["people"]
	await client.post(f"/v1/people/{tunde['id']}/contact", json={})
	await client.patch("/v1/me", json={"faith_enabled": True})
	prayer = await client.post(
		"/v1/faith/prayers", json={"title": "Tunde's move", "person_id": tunde["id"]}
	)
	assert prayer.status_code == 201, prayer.text

	assert (await client.delete(f"/v1/people/{tunde['id']}")).status_code == 204

	assert (await client.get(f"/v1/people/{tunde['id']}")).status_code == 404
	assert (await client.get("/v1/people")).json()["people"] == []
	assert (await client.get(f"/v1/captures/{note}")).json()["people"] == []
	assert (await client.get("/v1/people/unresolved")).json() == []
	async with admin_engine.connect() as conn:
		mentions = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE kind = 'mention'")
		)
		kept = (
			await conn.execute(
				text("SELECT person_id FROM derivations WHERE id = :p"),
				{"p": prayer.json()["id"]},
			)
		).one()
	assert mentions == 0, "the note's mention and the logged contact both go"
	assert kept == (None,), "the prayer stays, no longer linked to anyone"

	again = await _note(client, session, "Tunde called", reader)
	(new,) = (await client.get("/v1/people")).json()["people"]
	assert new["id"] != tunde["id"], "a later note meets them as someone new"
	assert (await client.get(f"/v1/captures/{again}")).json()["people"]


async def test_a_person_can_only_be_deleted_by_their_owner(
	client: AsyncClient,
	session: AsyncSession,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await _note(client, session, "Tunde", FakePeopleReader(_read(_mention("Tunde"))))
	(mine,) = (await client.get("/v1/people")).json()["people"]
	as_user(second_user)
	assert (await client.delete(f"/v1/people/{mine['id']}")).status_code == 404
	as_user(SEED_USER_ID)
	assert (await client.get(f"/v1/people/{mine['id']}")).status_code == 200
