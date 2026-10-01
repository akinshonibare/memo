"""Lists (ADR 0069): the gate, one name for one list, filing from a note, the Lists
routes, ticking, removing as a label, cascade, and isolation."""

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.schemas.derivations import Extraction
from memo.schemas.lists import ListItemFound, ListsExtraction
from memo.services.lists import display_name, list_key, might_list
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import TerminalError
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeListsReader,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


def _found(list_name: str, title: str, quantity: str | None = None) -> ListItemFound:
	return ListItemFound(list=list_name, title=title, quantity=quantity)


async def _note(
	client: AsyncClient,
	session: AsyncSession,
	body: str,
	*,
	lists: FakeListsReader | None = None,
) -> tuple[str, FakeListsReader]:
	reader = lists or FakeListsReader()
	made = await client.post(
		"/v1/captures", json={"text": body, "client_token": f"tok-{uuid.uuid4()}"}
	)
	assert made.status_code == 202, made.text
	cid = made.json()["id"]
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(Extraction()),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(None),
		lists=reader,
	)
	outcome = await process_capture(ctx, uuid.UUID(cid), SEED_USER_ID)
	assert outcome.status == "ready", outcome
	return str(cid), reader


# --- the gate and the names -----------------------------------------------------------


@pytest.mark.parametrize(
	"text_",
	[
		"add milk and eggs to the shopping list",
		"we're out of coffee",
		"Kemi says I should read Piranesi, adding it to my books",
		"gift idea for Mum: that blue scarf",
		"need to get a birthday card on the way home",
	],
)
def test_list_sounding_notes_pass_the_gate(text_: str) -> None:
	assert might_list(text_)


@pytest.mark.parametrize(
	"text_",
	[
		"call Tunde about pricing",
		"dentist at 2:30 tomorrow",
		"long day, tired",
		"",
		None,
	],
)
def test_other_notes_never_pay_for_the_reader(text_: str | None) -> None:
	assert not might_list(text_)


@pytest.mark.parametrize(
	("said", "key", "name"),
	[
		("the shopping list", "groceries", "groceries"),
		("Grocery", "groceries", "groceries"),
		("groceries", "groceries", "groceries"),
		("my books", "books", "books"),
		("to read", "books", "books"),
		("films to watch", "films to watch", "films to watch"),
		("Lagos trip", "lagos trip", "Lagos trip"),
		("gifts for Mum", "gifts for mum", "gifts for Mum"),
		("Packing list", "packing", "packing"),
	],
)
def test_one_name_means_one_list(said: str, key: str, name: str) -> None:
	assert list_key(said) == key
	assert display_name(said) == name


# --- filing from a note ---------------------------------------------------------------


async def test_items_are_filed_onto_the_list_their_name_means(
	client: AsyncClient, session: AsyncSession
) -> None:
	reader = FakeListsReader(
		ListsExtraction(
			items=[
				_found("shopping list", "milk", "2 litres"),
				_found("groceries", "eggs"),
				_found("books", "Piranesi"),
			]
		)
	)
	cid, _ = await _note(
		client,
		session,
		"add milk and eggs to the shopping list, and Piranesi to read",
		lists=reader,
	)
	assert reader.calls == 1 and reader.seen_lists == [[]]  # no lists yet

	lists = (await client.get("/v1/lists")).json()["lists"]
	assert [(lst["name"], lst["open_count"]) for lst in lists] == [
		("books", 1),
		("groceries", 2),
	]
	groceries = next(lst for lst in lists if lst["name"] == "groceries")
	detail = (await client.get(f"/v1/lists/{groceries['id']}")).json()
	assert [
		(i["title"], i["quantity"], i["added_by_hand"]) for i in detail["items"]
	] == [
		("eggs", None, False),
		("milk", "2 litres", False),
	]
	assert all(i["capture_id"] == cid for i in detail["items"])


async def test_milk_said_twice_is_milk_once_until_ticked(
	client: AsyncClient, session: AsyncSession
) -> None:
	first = FakeListsReader(ListsExtraction(items=[_found("groceries", "milk")]))
	await _note(client, session, "we're out of milk", lists=first)
	again = FakeListsReader(
		ListsExtraction(items=[_found("the shopping list", "Milk")])
	)
	await _note(client, session, "add milk to the shopping list", lists=again)
	# The second note saw the list the first one made.
	assert again.seen_lists == [["groceries"]]
	lists = (await client.get("/v1/lists")).json()["lists"]
	assert [(lst["name"], lst["open_count"]) for lst in lists] == [("groceries", 1)]

	# Ticked off, milk can be needed again.
	detail = (await client.get(f"/v1/lists/{lists[0]['id']}")).json()
	item = detail["items"][0]
	ticked = await client.patch(
		f"/v1/lists/{lists[0]['id']}/items/{item['id']}", json={"done": True}
	)
	assert ticked.status_code == 200 and ticked.json()["done_at"] is not None
	third = FakeListsReader(ListsExtraction(items=[_found("groceries", "milk")]))
	await _note(client, session, "milk again, out of milk", lists=third)
	detail = (await client.get(f"/v1/lists/{lists[0]['id']}")).json()
	assert detail["open_count"] == 1 and detail["done_count"] == 1


async def test_a_note_with_no_list_words_skips_the_reader(
	client: AsyncClient, session: AsyncSession
) -> None:
	_, reader = await _note(client, session, "call Tunde about pricing before Thursday")
	assert reader.calls == 0


async def test_a_reader_failure_never_costs_the_note(
	client: AsyncClient, session: AsyncSession
) -> None:
	cid, _ = await _note(
		client,
		session,
		"add bread to the list",
		lists=FakeListsReader(fail_with=TerminalError("boom")),
	)
	shown = (await client.get(f"/v1/captures/{cid}")).json()
	assert shown["status"] == "ready"
	assert (await client.get("/v1/lists")).json()["lists"] == []


async def test_list_items_never_reach_the_generic_item_routes(
	client: AsyncClient, session: AsyncSession
) -> None:
	cid, _ = await _note(
		client,
		session,
		"add bread to the list",
		lists=FakeListsReader(ListsExtraction(items=[_found("groceries", "bread")])),
	)
	items = (await client.get("/v1/derivations")).json()["items"]
	assert all(d["kind"] != "list_item" for d in items)
	shown = (await client.get(f"/v1/captures/{cid}")).json()
	assert all(d["kind"] != "list_item" for d in shown["derivations"])


# --- the routes -----------------------------------------------------------------------


async def test_a_list_you_name_and_items_you_type(client: AsyncClient) -> None:
	made = await client.post("/v1/lists", json={"name": "The Shopping List"})
	assert made.status_code == 201 and made.json()["name"] == "groceries"
	again = await client.post("/v1/lists", json={"name": "grocery"})
	assert again.json()["id"] == made.json()["id"]  # the same list, not a twin
	assert (await client.post("/v1/lists", json={"name": "   "})).status_code == 422

	list_id = made.json()["id"]
	item = await client.post(f"/v1/lists/{list_id}/items", json={"title": "Bread"})
	assert item.status_code == 201
	assert item.json()["added_by_hand"] is True and item.json()["capture_id"] is None
	twin = await client.post(f"/v1/lists/{list_id}/items", json={"title": "bread"})
	assert twin.json()["id"] == item.json()["id"]

	reworded = await client.patch(
		f"/v1/lists/{list_id}/items/{item.json()['id']}",
		json={"title": "sourdough", "quantity": "1 loaf"},
	)
	assert (reworded.json()["title"], reworded.json()["quantity"]) == (
		"sourdough",
		"1 loaf",
	)
	assert (
		await client.patch(f"/v1/lists/{list_id}/items/{item.json()['id']}", json={})
	).status_code == 422


async def test_rename_archive_and_the_refused_merge(client: AsyncClient) -> None:
	a = (await client.post("/v1/lists", json={"name": "Lagos trip"})).json()
	b = (await client.post("/v1/lists", json={"name": "books"})).json()
	renamed = await client.patch(
		f"/v1/lists/{a['id']}", json={"name": "Lagos, December"}
	)
	assert renamed.json()["name"] == "Lagos, December"
	clash = await client.patch(f"/v1/lists/{a['id']}", json={"name": "Books"})
	assert clash.status_code == 409

	archived = await client.patch(f"/v1/lists/{b['id']}", json={"archived": True})
	assert archived.json()["archived"] is True
	names = [lst["name"] for lst in (await client.get("/v1/lists")).json()["lists"]]
	assert names == ["Lagos, December"]
	with_archived = (await client.get("/v1/lists?archived=true")).json()["lists"]
	assert len(with_archived) == 2
	# Naming it again brings it back.
	back = await client.post("/v1/lists", json={"name": "books"})
	assert back.json()["id"] == b["id"] and back.json()["archived"] is False


async def test_removing_an_item_is_a_label_and_a_list_takes_its_items_with_it(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	await _note(
		client,
		session,
		"add bread and a card to the list",
		lists=FakeListsReader(
			ListsExtraction(
				items=[_found("groceries", "bread"), _found("groceries", "card")]
			)
		),
	)
	lst = (await client.get("/v1/lists")).json()["lists"][0]
	detail = (await client.get(f"/v1/lists/{lst['id']}")).json()
	bread = next(i for i in detail["items"] if i["title"] == "bread")
	typed = (
		await client.post(f"/v1/lists/{lst['id']}/items", json={"title": "gum"})
	).json()

	assert (
		await client.delete(f"/v1/lists/{lst['id']}/items/{bread['id']}")
	).status_code == 204
	assert (
		await client.delete(f"/v1/lists/{lst['id']}/items/{typed['id']}")
	).status_code == 204
	async with admin_engine.connect() as conn:
		rows = (
			await conn.execute(
				text(
					"SELECT payload->>'title', status FROM derivations "
					"WHERE kind = 'list_item' ORDER BY 1"
				)
			)
		).all()
	# memo's item is kept as rejected (a label); the typed one is simply gone.
	assert rows == [("bread", "rejected"), ("card", "accepted")]
	detail = (await client.get(f"/v1/lists/{lst['id']}")).json()
	assert [i["title"] for i in detail["items"]] == ["card"]

	assert (await client.delete(f"/v1/lists/{lst['id']}")).status_code == 204
	assert (await client.get(f"/v1/lists/{lst['id']}")).status_code == 404
	async with admin_engine.connect() as conn:
		left = await conn.scalar(
			text("SELECT count(*) FROM derivations WHERE kind = 'list_item'")
		)
	assert left == 0


async def test_another_persons_lists_are_invisible(
	client: AsyncClient, as_user: Callable[[uuid.UUID], None], second_user: uuid.UUID
) -> None:
	mine = (await client.post("/v1/lists", json={"name": "groceries"})).json()
	as_user(second_user)
	assert (await client.get("/v1/lists")).json()["lists"] == []
	assert (await client.get(f"/v1/lists/{mine['id']}")).status_code == 404
	assert (
		await client.post(f"/v1/lists/{mine['id']}/items", json={"title": "x"})
	).status_code == 404
