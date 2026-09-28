"""Review and correction: each call is a labelled example arriving for free."""

import uuid
from collections.abc import Callable

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID


async def _capture(client: AsyncClient, token: str) -> str:
	created = await client.post(
		"/v1/captures", json={"text": "call Tunde", "client_token": token}
	)
	capture_id: str = created.json()["id"]
	return capture_id


async def _seed_derivation(
	admin_engine: AsyncEngine,
	capture_id: str,
	*,
	kind: str = "todo",
	title: str = "Call Tunde",
	user_id: uuid.UUID = SEED_USER_ID,
) -> str:
	derivation_id = uuid.uuid4()
	payload = (
		{"title": title, "due": None, "notes": None}
		if kind == "todo"
		else {"title": title, "summary": "s"}
	)
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO derivations (id, user_id, capture_id, kind, payload, "
				"status, extractor_version, confidence) VALUES "
				"(:id, :user_id, :capture_id, :kind, CAST(:payload AS jsonb), "
				"'pending_review', 'extract-v1/test', 0.8)"
			),
			{
				"id": derivation_id,
				"user_id": user_id,
				"capture_id": capture_id,
				"kind": kind,
				"payload": __import__("json").dumps(payload),
			},
		)
	return str(derivation_id)


async def test_list_filters_by_status_and_kind(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	capture_id = await _capture(client, "tok-drv-1")
	todo = await _seed_derivation(admin_engine, capture_id)
	idea = await _seed_derivation(admin_engine, capture_id, kind="idea", title="Idea")

	pending = await client.get("/v1/derivations", params={"status": "pending_review"})
	assert {d["id"] for d in pending.json()["items"]} == {todo, idea}
	todos = await client.get("/v1/derivations", params={"kind": "todo"})
	assert [d["id"] for d in todos.json()["items"]] == [todo]
	assert (
		await client.get("/v1/derivations", params={"kind": "habit"})
	).status_code == 422


async def test_confirm_reject_and_edit(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	capture_id = await _capture(client, "tok-drv-2")
	derivation_id = await _seed_derivation(admin_engine, capture_id)

	confirmed = await client.patch(
		f"/v1/derivations/{derivation_id}", json={"status": "confirmed"}
	)
	assert confirmed.status_code == 200
	assert confirmed.json()["status"] == "confirmed"
	assert confirmed.json()["reviewed_at"] is not None

	edited = await client.patch(
		f"/v1/derivations/{derivation_id}",
		json={"payload": {"title": "  Call Tunde about pricing ", "due": "2026-10-01"}},
	)
	assert edited.status_code == 200
	assert edited.json()["payload"] == {
		"title": "Call Tunde about pricing",
		"due": "2026-10-01",
		"notes": None,
	}
	assert edited.json()["status"] == "confirmed"

	rejected = await client.patch(
		f"/v1/derivations/{derivation_id}", json={"status": "rejected"}
	)
	assert rejected.json()["status"] == "rejected"


async def test_edit_is_validated_with_the_kinds_model(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	capture_id = await _capture(client, "tok-drv-3")
	derivation_id = await _seed_derivation(admin_engine, capture_id)
	bad = await client.patch(
		f"/v1/derivations/{derivation_id}", json={"payload": {"title": "   "}}
	)
	assert bad.status_code == 422
	empty = await client.patch(f"/v1/derivations/{derivation_id}", json={})
	assert empty.status_code == 422


async def test_adding_a_missed_derivation_is_recorded_as_user_created(
	client: AsyncClient,
) -> None:
	capture_id = await _capture(client, "tok-drv-4")
	created = await client.post(
		f"/v1/captures/{capture_id}/derivations",
		json={"kind": "todo", "payload": {"title": "Email Sarah the invoice"}},
	)
	assert created.status_code == 201, created.text
	body = created.json()
	assert body["status"] == "user_created"
	assert body["extractor_version"] == "user"
	assert body["confidence"] is None

	detail = (await client.get(f"/v1/captures/{capture_id}")).json()
	assert [d["id"] for d in detail["derivations"]] == [body["id"]]

	invalid = await client.post(
		f"/v1/captures/{capture_id}/derivations",
		json={"kind": "event", "payload": {"title": "no time"}},
	)
	assert invalid.status_code == 422


async def test_other_users_derivations_are_invisible(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	capture_id = await _capture(client, "tok-drv-5")
	derivation_id = await _seed_derivation(admin_engine, capture_id)

	as_user(second_user)
	assert (await client.get("/v1/derivations")).json()["items"] == []
	assert (
		await client.patch(
			f"/v1/derivations/{derivation_id}", json={"status": "confirmed"}
		)
	).status_code == 404
