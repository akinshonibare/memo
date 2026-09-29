"""Faith (ADR 0042): the switch, the faith space, passages, prayers, the reading plan,
the one thing a day, and the weekly section."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import Extraction, IdeaPayload, TodoPayload
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.scripture import plan
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
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
		people=FakePeopleReader(),
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


# --- the reading plan -----------------------------------------------------------------


async def test_the_plan_moves_by_position_and_undo_takes_one_back(
	client: AsyncClient,
) -> None:
	state = (await client.get("/v1/faith")).json()["plan"]
	assert state["position"] == 0 and state["cycle"] == 1
	assert state["next"]["label"] == "Genesis 1–4"
	assert state["next"]["book"] == "GEN" and state["next"]["chapter"] == 1
	assert state["last"] is None and state["total"] == len(plan())

	first = (await client.post("/v1/faith/readings")).json()
	assert first["position"] == 1 and first["last"]["label"] == "Genesis 1–4"
	second = (await client.post("/v1/faith/readings")).json()
	assert second["position"] == 2
	undone = (await client.delete("/v1/faith/readings/last")).json()
	assert (
		undone["position"] == 1 and undone["next"]["label"] == second["last"]["label"]
	)
	(await client.delete("/v1/faith/readings/last"))
	empty = (await client.delete("/v1/faith/readings/last")).json()
	assert empty["position"] == 0, "nothing marked: nothing to undo"


async def test_finishing_the_plan_starts_again_at_genesis(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	total = len(plan())
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO faith_readings (id, user_id, plan, cycle, position) "
				"SELECT gen_random_uuid(), :u, 'canonical', 1, g "
				"FROM generate_series(0, :last) g"
			),
			{"u": SEED_USER_ID, "last": total - 1},
		)
	state = (await client.get("/v1/faith")).json()["plan"]
	assert state["cycle"] == 2 and state["position"] == 0
	assert state["next"]["label"] == "Genesis 1–4"
	assert state["last"]["label"].startswith("Revelation")
	again = (await client.post("/v1/faith/readings")).json()
	assert again["cycle"] == 2 and again["position"] == 1


async def test_readings_are_the_users_own(
	client: AsyncClient,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await client.post("/v1/faith/readings")
	as_user(second_user)
	assert (await client.get("/v1/faith")).json()["plan"]["position"] == 0


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
	await client.post("/v1/faith/readings")
	prayer = (await client.post("/v1/faith/prayers", json={"title": "rain"})).json()
	await client.patch(f"/v1/faith/prayers/{prayer['id']}", json={"answered": True})
	await _note(client, session, "1 Cor 13 and Romans 8:28 today", space="faith")

	assert (await client.get("/v1/review/week")).json()["faith"] is None
	await _faith(client, True)
	faith = (await client.get("/v1/review/week")).json()["faith"]
	assert [r["label"] for r in faith["read"]] == ["Genesis 1–4"]
	assert [p["title"] for p in faith["answered"]] == ["rain"]
	assert [p["passage"]["ref"] for p in faith["passages"]] == [
		"1 Corinthians 13",
		"Romans 8:28",
	]


# --- export and deletion --------------------------------------------------------------


async def test_export_carries_the_readings_and_delete_removes_them(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	await client.post("/v1/faith/readings")
	await client.post("/v1/faith/prayers", json={"title": "kept in the export"})
	exported = (await client.get("/v1/me/export")).json()
	assert [r["position"] for r in exported["faith_readings"]] == [0]
	assert [d["payload"]["title"] for d in exported["derivations_without_note"]] == [
		"kept in the export"
	]
	wiped = await client.delete(f"/v1/me/data?confirm={SEED_USER_ID}")
	assert wiped.json()["deleted"]["faith_readings"] == 1
	async with admin_engine.connect() as conn:
		left = await conn.scalar(text("SELECT count(*) FROM faith_readings"))
	assert left == 0
