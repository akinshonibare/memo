"""Faith (ADR 0042): the switch, the faith space, passages, prayers, the reading plan,
the one thing a day, and the weekly section."""

import datetime as dt
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
from memo.models import Capture
from memo.schemas.derivations import Extraction, IdeaPayload, TodoPayload
from memo.schemas.faith import FaithExtraction, PrayerFound
from memo.schemas.people import MentionPayload, PeopleExtraction
from memo.services.faith import apply_prayers
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import TerminalError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeFaithReader,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

REFLECTION = "Romans 8:28 this morning. All things work together; I felt grace."
ERRAND = "pick up the dry cleaning on the way home"


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
	space: str = "main",
	extraction: Extraction | None = None,
	people: PeopleExtraction | None = None,
	faith: FakeFaithReader | None = None,
) -> str:
	made = await client.post(
		"/v1/captures",
		json={"text": body, "client_token": f"tok-{uuid.uuid4()}", "space": space},
	)
	assert made.status_code == 202, made.text
	cid = made.json()["id"]
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction or Extraction()),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(people),
		faith=faith or FakeFaithReader(),
	)
	outcome = await process_capture(ctx, uuid.UUID(cid), SEED_USER_ID)
	assert outcome.status == "ready", outcome
	return str(cid)


async def _faith(client: AsyncClient, on: bool) -> None:
	r = await client.patch("/v1/me", json={"faith_enabled": on})
	assert r.status_code == 200 and r.json()["faith_enabled"] is on


async def _person(
	admin: AsyncEngine, name: str, user_id: uuid.UUID = SEED_USER_ID
) -> str:
	pid = uuid.uuid4()
	async with admin.begin() as conn:
		await conn.execute(
			text("INSERT INTO people (id, user_id, name) VALUES (:id, :u, :n)"),
			{"id": pid, "u": user_id, "n": name},
		)
	return str(pid)


async def _backdate(admin: AsyncEngine, table: str, row_id: str, days: int) -> None:
	async with admin.begin() as conn:
		await conn.execute(
			text(
				f"UPDATE {table} "
				"SET created_at = created_at - make_interval(days => :d) "
				"WHERE id = :id"
			),
			{"d": days, "id": row_id},
		)


# --- the switch -----------------------------------------------------------------------


async def test_faith_is_off_until_turned_on_and_the_translation_is_niv(
	client: AsyncClient,
) -> None:
	me = (await client.get("/v1/me")).json()
	assert me["faith_enabled"] is False
	assert me["bible_translation"] == "NIV"
	changed = await client.patch(
		"/v1/me", json={"faith_enabled": True, "bible_translation": "ESV"}
	)
	assert changed.json()["faith_enabled"] is True
	assert changed.json()["bible_translation"] == "ESV"
	assert (
		await client.patch("/v1/me", json={"bible_translation": "Klingon"})
	).status_code == 422
	assert (
		await client.patch("/v1/me", json={"faith_enabled": None})
	).status_code == 422


# --- the faith space ------------------------------------------------------------------


async def test_faith_notes_leave_home_only_while_faith_is_on(
	client: AsyncClient, session: AsyncSession
) -> None:
	errand = await _note(client, session, ERRAND)
	reflection = await _note(client, session, REFLECTION, space="faith")

	async def home() -> list[str]:
		return [c["id"] for c in (await client.get("/v1/captures")).json()["items"]]

	assert set(await home()) == {errand, reflection}, "off: every note is a note"
	await _faith(client, True)
	assert await home() == [errand]
	faith = (await client.get("/v1/captures?space=faith")).json()["items"]
	assert [c["id"] for c in faith] == [reflection]
	assert faith[0]["space"] == "faith"
	await _faith(client, False)
	assert set(await home()) == {errand, reflection}, "off again: nothing lost"


async def test_search_and_ask_leave_faith_notes_out_while_faith_is_on(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _note(client, session, ERRAND)
	reflection = await _note(client, session, REFLECTION, space="faith")
	async with admin_engine.connect() as conn:
		spaces = (
			await conn.execute(
				text("SELECT DISTINCT space FROM chunks WHERE capture_id = :id"),
				{"id": reflection},
			)
		).all()
	assert [r[0] for r in spaces] == ["faith"], "the chunks carry the note's space"

	async def found(q: str) -> set[str]:
		hits = (await client.post("/v1/search", json={"q": q, "limit": 10})).json()
		return {h["capture"]["id"] for h in hits if h["capture"]}

	assert reflection in await found("grace")
	await _faith(client, True)
	assert reflection not in await found("grace")
	await _faith(client, False)
	assert reflection in await found("grace")


async def test_a_faith_note_gets_passages_and_keeps_todos_but_not_ideas(
	client: AsyncClient, session: AsyncSession
) -> None:
	extraction = Extraction(
		todos=[TodoPayload(title="Text Sam about Thursday")],
		ideas=[IdeaPayload(title="Grace", summary="All things work together.")],
	)
	faith_note = await _note(
		client, session, REFLECTION, space="faith", extraction=extraction
	)
	detail = (await client.get(f"/v1/captures/{faith_note}")).json()
	assert [p["ref"] for p in detail["passages"]] == ["Romans 8:28"]
	assert detail["passages"][0]["book"] == "ROM"
	assert [d["kind"] for d in detail["derivations"]] == ["todo"], (
		"no idea from a reflection, and a passage is never an item"
	)

	main_note = await _note(client, session, REFLECTION, extraction=extraction)
	main = (await client.get(f"/v1/captures/{main_note}")).json()
	assert main["passages"] == [], "a note outside Faith has no passages"
	assert sorted(d["kind"] for d in main["derivations"]) == ["idea", "todo"]

	items = (await client.get("/v1/derivations?limit=200")).json()["items"]
	assert {d["kind"] for d in items} <= {"todo", "event", "idea"}


async def test_moving_a_note_moves_its_chunks_and_finds_its_passages_again(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	cid = await _note(client, session, REFLECTION)
	moved = await client.post(f"/v1/captures/{cid}/move", json={"space": "faith"})
	assert moved.status_code == 200 and moved.json()["space"] == "faith"
	detail = (await client.get(f"/v1/captures/{cid}")).json()
	assert [p["ref"] for p in detail["passages"]] == ["Romans 8:28"]
	async with admin_engine.connect() as conn:
		spaces = {
			r[0]
			for r in (
				await conn.execute(
					text(
						"SELECT space FROM chunks WHERE capture_id = :id "
						"AND derivation_id IS NULL AND superseded_at IS NULL"
					),
					{"id": cid},
				)
			).all()
		}
	assert spaces == {"faith"}

	back = await client.post(f"/v1/captures/{cid}/move", json={"space": "main"})
	assert back.json()["space"] == "main"
	assert (await client.get(f"/v1/captures/{cid}")).json()["passages"] == []
	assert (
		await client.post(f"/v1/captures/{cid}/move", json={"space": "heaven"})
	).status_code == 422


async def test_on_this_day_leaves_faith_notes_out_while_faith_is_on(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	cid = await _note(client, session, REFLECTION, space="faith")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE captures SET created_at = created_at - interval '1 year' "
				"WHERE id = :id"
			),
			{"id": cid},
		)
	looked = (await client.get("/v1/morning")).json()["on_this_day"]
	assert looked is not None and looked["capture_id"] == cid
	await _faith(client, True)
	assert (await client.get("/v1/morning")).json()["on_this_day"] is None


# --- prayers --------------------------------------------------------------------------


async def test_a_prayer_is_answered_not_done_and_can_name_someone(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await _faith(client, True)
	mum = await _person(admin_engine, "Tunde")
	made = await client.post(
		"/v1/faith/prayers",
		json={"title": "Tunde's mum's surgery", "notes": "Thursday", "person_id": mum},
	)
	assert made.status_code == 201, made.text
	prayer = made.json()
	assert prayer["person_name"] == "Tunde" and prayer["answered_at"] is None

	faith = (await client.get("/v1/faith")).json()
	assert [p["id"] for p in faith["open"]] == [prayer["id"]]
	assert faith["answered"] == []
	person = (await client.get(f"/v1/people/{mum}")).json()
	assert [p["title"] for p in person["prayers"]] == ["Tunde's mum's surgery"]

	answered = await client.patch(
		f"/v1/faith/prayers/{prayer['id']}",
		json={"answered": True, "answer": "It went well"},
	)
	assert answered.json()["answered_at"] is not None
	assert answered.json()["answer"] == "It went well"
	faith = (await client.get("/v1/faith")).json()
	assert faith["open"] == [] and faith["answered"][0]["id"] == prayer["id"]

	unanswered = await client.patch(
		f"/v1/faith/prayers/{prayer['id']}", json={"answered": False}
	)
	assert unanswered.json()["answered_at"] is None
	assert unanswered.json()["answer"] is None, "how it was answered no longer applies"

	cleared = await client.patch(
		f"/v1/faith/prayers/{prayer['id']}", json={"person_id": None}
	)
	assert cleared.json()["person_id"] is None

	removed = await client.patch(
		f"/v1/faith/prayers/{prayer['id']}", json={"removed": True}
	)
	assert removed.status_code == 200
	faith = (await client.get("/v1/faith")).json()
	assert faith["open"] == [] and faith["answered"] == []


async def test_prayers_validate_and_stay_the_users_own(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	assert (
		await client.post("/v1/faith/prayers", json={"title": "  "})
	).status_code == 422
	theirs = await _person(admin_engine, "Sam", second_user)
	assert (
		await client.post(
			"/v1/faith/prayers", json={"title": "for Sam", "person_id": theirs}
		)
	).status_code == 404, "no praying for someone else's person"
	mine = (await client.post("/v1/faith/prayers", json={"title": "mine"})).json()
	as_user(second_user)
	assert (
		await client.patch(f"/v1/faith/prayers/{mine['id']}", json={"answered": True})
	).status_code == 404
	assert (await client.get("/v1/faith")).json()["open"] == []


async def test_person_pages_show_prayers_only_while_faith_is_on(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	pid = await _person(admin_engine, "Kemi")
	await client.post("/v1/faith/prayers", json={"title": "Kemi", "person_id": pid})
	assert (await client.get(f"/v1/people/{pid}")).json()["prayers"] == []
	await _faith(client, True)
	assert len((await client.get(f"/v1/people/{pid}")).json()["prayers"]) == 1


async def test_the_generic_item_routes_never_touch_faith_rows(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _note(client, session, REFLECTION, space="faith")
	prayer = (await client.post("/v1/faith/prayers", json={"title": "x"})).json()
	listed = (await client.get("/v1/derivations?limit=200")).json()["items"]
	assert all(d["kind"] not in ("prayer", "passage") for d in listed)
	refused = await client.patch(
		f"/v1/derivations/{prayer['id']}", json={"status": "rejected"}
	)
	assert refused.status_code == 422


# --- what you're reading -------------------------------------------------------------


async def _reading(client: AsyncClient) -> dict[str, Any]:
	return dict((await client.get("/v1/faith")).json()["reading"])


async def test_nothing_is_set_until_you_choose_what_you_are_reading(
	client: AsyncClient,
) -> None:
	state = await _reading(client)
	assert state == {"current": None, "next": None, "last": None, "recent": []}
	nothing = await client.post("/v1/faith/readings", json={})
	assert nothing.status_code == 422, "nothing set: nothing to read next"
	bad = await client.put("/v1/faith/reading", json={"ref": "the one with the ark"})
	assert bad.status_code == 422


async def test_you_set_a_book_and_read_it_a_chapter_at_a_time(
	client: AsyncClient,
) -> None:
	chosen = (await client.put("/v1/faith/reading", json={"ref": "John"})).json()
	assert chosen["current"]["ref"] == "John" and chosen["next"]["ref"] == "John 1"
	assert chosen["next"]["book"] == "JHN" and chosen["next"]["chapter"] == 1

	first = await client.post("/v1/faith/readings", json={})
	assert first.status_code == 201
	state = first.json()
	assert state["last"]["passage"]["ref"] == "John 1"
	assert state["next"]["ref"] == "John 2"
	again = (await client.post("/v1/faith/readings", json={})).json()
	assert again["next"]["ref"] == "John 3"
	twice = (await client.post("/v1/faith/readings", json={"ref": "John 2"})).json()
	assert len(twice["recent"]) == 2, "the same passage on the same day is one reading"

	# Something else read today doesn't move John on; three chapters at once do.
	other = (await client.post("/v1/faith/readings", json={"ref": "Psalm 23"})).json()
	assert other["last"]["passage"]["ref"] == "Psalm 23"
	assert other["next"]["ref"] == "John 3"
	more = (await client.post("/v1/faith/readings", json={"ref": "John 3-5"})).json()
	assert more["next"]["ref"] == "John 6"

	# Undo takes the latest back; John goes back to where it was.
	undone = (await client.delete(f"/v1/faith/readings/{more['last']['id']}")).json()
	assert undone["next"]["ref"] == "John 3"
	assert [r["passage"]["ref"] for r in undone["recent"]] == [
		"Psalm 23",
		"John 2",
		"John 1",
	]


async def test_changing_what_you_read_keeps_what_you_read_before(
	client: AsyncClient,
) -> None:
	await client.put("/v1/faith/reading", json={"ref": "Romans 8"})
	await client.post("/v1/faith/readings", json={})
	finished = await _reading(client)
	assert finished["next"] is None, "a chapter set is read once"
	changed = (await client.put("/v1/faith/reading", json={"ref": "James"})).json()
	assert changed["current"]["ref"] == "James" and changed["next"]["ref"] == "James 1"
	assert [r["passage"]["ref"] for r in changed["recent"]] == ["Romans 8"]
	cleared = (await client.put("/v1/faith/reading", json={"ref": None})).json()
	assert cleared["current"] is None and len(cleared["recent"]) == 1


async def test_a_reading_on_an_earlier_day_and_never_a_future_one(
	client: AsyncClient,
) -> None:
	today = dt.datetime.now(dt.UTC).date()  # the seed user is on UTC
	yesterday = (today - dt.timedelta(days=1)).isoformat()
	state = (
		await client.post(
			"/v1/faith/readings", json={"ref": "Genesis 1", "on": yesterday}
		)
	).json()
	assert state["last"]["on"] == yesterday
	future = (today + dt.timedelta(days=1)).isoformat()
	assert (
		await client.post("/v1/faith/readings", json={"ref": "Genesis 2", "on": future})
	).status_code == 422


async def test_readings_are_the_users_own(
	client: AsyncClient,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await client.put("/v1/faith/reading", json={"ref": "John"})
	mine = (await client.post("/v1/faith/readings", json={})).json()["last"]["id"]
	as_user(second_user)
	state = await _reading(client)
	assert state["current"] is None and state["recent"] == []
	assert (await client.delete(f"/v1/faith/readings/{mine}")).status_code == 404


async def test_the_generic_item_routes_never_touch_a_reading(
	client: AsyncClient,
) -> None:
	mine = (await client.post("/v1/faith/readings", json={"ref": "Mark 1"})).json()[
		"last"
	]["id"]
	refused = await client.patch(
		f"/v1/derivations/{mine}", json={"status": "confirmed"}
	)
	assert refused.status_code == 422
	listed = (await client.get("/v1/derivations")).json()
	assert all(d["id"] != mine for d in listed["items"])


# --- the passages your notes talk about -----------------------------------------------


async def test_passages_are_a_reference_to_the_notes_that_name_them(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _faith(client, True)
	older = await _note(client, session, "Psalm 23 before the interview", space="faith")
	await _backdate(admin_engine, "captures", older, 3)
	newer = await _note(
		client, session, "Psalm 23 again, and Romans 8:28 on the train", space="faith"
	)
	passages = (await client.get("/v1/faith")).json()["passages"]
	assert [p["passage"]["ref"] for p in passages] == ["Psalm 23", "Romans 8:28"]
	psalm = passages[0]
	assert psalm["count"] == 2
	assert [n["capture_id"] for n in psalm["notes"]] == [newer, older]
	assert psalm["notes"][1]["excerpt"].startswith("Psalm 23 before")


# --- the one thing a day --------------------------------------------------------------


async def test_the_moment_is_a_prayer_from_this_day_in_another_year_first(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _faith(client, True)
	assert (await client.get("/v1/faith")).json()["moment"] is None, "nothing yet"

	# A passage written about ten days ago can come back...
	older = await _note(client, session, "Psalm 23 before the interview", space="faith")
	await _backdate(admin_engine, "captures", older, 10)
	moment = (await client.get("/v1/faith")).json()["moment"]
	assert moment["kind"] == "passage" and moment["passage"]["ref"] == "Psalm 23"
	assert moment["capture_id"] == older
	assert moment["excerpt"].startswith("Psalm 23")
	# ...but not one from yesterday.
	fresh = await _note(client, session, "John 15 last night", space="faith")
	await _backdate(admin_engine, "captures", fresh, 1)
	assert (await client.get("/v1/faith")).json()["moment"]["passage"]["ref"] == (
		"Psalm 23"
	)

	# A prayer prayed on this date last year comes first, answered or not.
	prayer = (
		await client.post("/v1/faith/prayers", json={"title": "the new job"})
	).json()
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE derivations SET created_at = created_at - interval '1 year' "
				"WHERE id = :id"
			),
			{"id": prayer["id"]},
		)
	moment = (await client.get("/v1/faith")).json()["moment"]
	assert moment["kind"] == "prayer" and moment["prayer"]["id"] == prayer["id"]
	today = dt.datetime.now(dt.UTC).date()  # the seed user's zone is UTC
	assert moment["on"] == today.replace(year=today.year - 1).isoformat()


# --- the weekly review ----------------------------------------------------------------


async def test_the_weekly_review_has_a_faith_section_only_while_faith_is_on(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.post("/v1/faith/readings", json={"ref": "Genesis 1-4"})
	prayer = (await client.post("/v1/faith/prayers", json={"title": "rain"})).json()
	await client.patch(f"/v1/faith/prayers/{prayer['id']}", json={"answered": True})
	await _note(client, session, "1 Cor 13 and Romans 8:28 today", space="faith")

	assert (await client.get("/v1/review/week")).json()["faith"] is None
	await _faith(client, True)
	faith = (await client.get("/v1/review/week")).json()["faith"]
	assert [r["passage"]["ref"] for r in faith["read"]] == ["Genesis 1–4"]
	assert [p["title"] for p in faith["answered"]] == ["rain"]
	assert [p["passage"]["ref"] for p in faith["passages"]] == [
		"1 Corinthians 13",
		"Romans 8:28",
	]


# --- export and deletion --------------------------------------------------------------


async def test_export_carries_the_readings_and_delete_removes_them(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await client.put("/v1/faith/reading", json={"ref": "John"})
	await client.post("/v1/faith/readings", json={})
	await client.post("/v1/faith/prayers", json={"title": "kept in the export"})
	exported = (await client.get("/v1/me/export")).json()
	assert sorted(
		d["payload"].get("title") or d["payload"]["ref"]
		for d in exported["derivations_without_note"]
	) == ["John 1", "kept in the export"]
	wiped = await client.delete(f"/v1/me/data?confirm={SEED_USER_ID}")
	assert wiped.status_code == 200
	async with admin_engine.connect() as conn:
		left = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE kind = 'reading'")
		)
	assert left == 0


# --- prayers found in notes ----------------------------------------------------------

KEMI_NOTE = "Kemi asked me to pray for her exams next week"
KEMI = PeopleExtraction(people=[MentionPayload(name="Kemi", contact="none")])
PRAYER_TODO = Extraction(todos=[TodoPayload(title="Pray for Kemi's exams")])


def _found(title: str, for_person: str | None = None) -> FakeFaithReader:
	return FakeFaithReader(
		FaithExtraction(prayers=[PrayerFound(title=title, for_person=for_person)])
	)


async def test_a_prayer_said_on_home_is_filed_into_faith_for_the_person_named(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _faith(client, True)
	reader = _found("Kemi's exams next week", "Kemi")
	cid = await _note(
		client, session, KEMI_NOTE, extraction=PRAYER_TODO, people=KEMI, faith=reader
	)
	assert reader.calls == 1
	(prayer,) = (await client.get("/v1/faith")).json()["open"]
	assert prayer["title"] == "Kemi's exams next week"
	assert prayer["person_name"] == "Kemi", "linked to the person the note names"
	assert prayer["capture_id"] == cid
	detail = (await client.get(f"/v1/captures/{cid}")).json()
	assert detail["derivations"] == [], "the 'Pray for...' todo is the prayer: it goes"
	assert detail["space"] == "main", "the note itself stays on Home"


async def test_with_faith_off_nothing_is_read_and_the_todo_stays(
	client: AsyncClient, session: AsyncSession
) -> None:
	reader = _found("Kemi's exams", "Kemi")
	cid = await _note(
		client, session, KEMI_NOTE, extraction=PRAYER_TODO, people=KEMI, faith=reader
	)
	assert reader.calls == 0, "Faith off: no model call"
	detail = (await client.get(f"/v1/captures/{cid}")).json()
	assert [d["payload"]["title"] for d in detail["derivations"]] == [
		"Pray for Kemi's exams"
	]


async def test_a_note_with_no_prayer_words_is_never_read(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _faith(client, True)
	reader = _found("should not happen")
	await _note(client, session, "need to call Tunde about pricing", faith=reader)
	assert reader.calls == 0


async def test_a_name_that_is_only_close_is_not_linked(
	client: AsyncClient, session: AsyncSession
) -> None:
	""" "Tunde's mum" is not Tunde: an exact match or nothing."""
	await _faith(client, True)
	await _note(
		client,
		session,
		"pray for Tunde's mum, surgery on Thursday",
		people=PeopleExtraction(people=[MentionPayload(name="Tunde", contact="none")]),
		faith=_found("Tunde's mum's surgery", "Tunde's mum"),
	)
	(prayer,) = (await client.get("/v1/faith")).json()["open"]
	assert prayer["person_id"] is None


async def test_a_prayer_you_acted_on_survives_its_note_being_read_again(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _faith(client, True)
	cid = await _note(
		client, session, KEMI_NOTE, people=KEMI, faith=_found("Kemi's exams", "Kemi")
	)
	(prayer,) = (await client.get("/v1/faith")).json()["open"]
	await client.patch(
		f"/v1/faith/prayers/{prayer['id']}", json={"answered": True, "answer": "passed"}
	)
	capture = await session.get(Capture, uuid.UUID(cid))
	assert capture is not None
	await apply_prayers(
		session,
		capture,
		FaithExtraction(prayers=[PrayerFound(title="Kemi's exams")]),
		version="faith-v1/fake",
		extract_version="extract-v1.1/fake",
	)
	await session.commit()
	await bind_user(session, SEED_USER_ID)
	faith = (await client.get("/v1/faith")).json()
	assert [p["answer"] for p in faith["answered"]] == ["passed"], "kept, answered"
	assert [p["title"] for p in faith["open"]] == ["Kemi's exams"], "and a fresh one"


async def test_a_reader_failure_never_costs_the_note(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _faith(client, True)
	cid = await _note(
		client,
		session,
		KEMI_NOTE,
		extraction=PRAYER_TODO,
		faith=FakeFaithReader(fail_with=TerminalError("model declined")),
	)
	detail = (await client.get(f"/v1/captures/{cid}")).json()
	assert detail["status"] == "ready"
	assert len(detail["derivations"]) == 1, "no prayer, so the todo stays"


async def test_passages_come_from_any_note_while_faith_is_on(
	client: AsyncClient, session: AsyncSession
) -> None:
	off = await _note(client, session, "Psalm 23 on the train")
	assert (await client.get(f"/v1/captures/{off}")).json()["passages"] == []
	await _faith(client, True)
	on = await _note(client, session, "Psalm 23 on the train again")
	detail = (await client.get(f"/v1/captures/{on}")).json()
	assert [p["ref"] for p in detail["passages"]] == ["Psalm 23"]
	assert detail["space"] == "main"
	week = (await client.get("/v1/review/week")).json()["faith"]
	assert [p["passage"]["ref"] for p in week["passages"]] == ["Psalm 23"]
