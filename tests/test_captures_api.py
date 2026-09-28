import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


async def test_v1_requires_the_bearer_token(client: AsyncClient) -> None:
	assert (
		await client.get("/v1/captures", headers={"Authorization": ""})
	).status_code == 401
	assert (
		await client.get("/v1/captures", headers={"Authorization": "Bearer wrong"})
	).status_code == 401


async def test_capture_round_trips(client: AsyncClient) -> None:
	created = await client.post(
		"/v1/captures",
		json={
			"text": "call Tunde about the pricing thing",
			"client_token": "tok-round-trip",
		},
	)
	assert created.status_code == 202
	body = created.json()
	assert body["status"] == "pending"
	assert body["kind"] == "text"
	assert body["attempt_count"] == 0

	fetched = await client.get(f"/v1/captures/{body['id']}")
	assert fetched.status_code == 200
	assert fetched.json()["text"] == "call Tunde about the pricing thing"


async def test_replaying_the_same_client_token_creates_one_row(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	payload = {"text": "buy milk", "client_token": "tok-replayed-twice"}
	first = await client.post("/v1/captures", json=payload)
	second = await client.post("/v1/captures", json=payload)

	assert first.status_code == second.status_code == 202
	assert first.json()["id"] == second.json()["id"]
	async with admin_engine.connect() as conn:
		count = await conn.scalar(text("SELECT count(*) FROM captures"))
	assert count == 1


async def test_user_id_in_the_body_is_rejected(client: AsyncClient) -> None:
	response = await client.post(
		"/v1/captures",
		json={
			"text": "x",
			"client_token": "tok-smuggled",
			"user_id": str(uuid.uuid4()),
		},
	)
	assert response.status_code == 422


async def test_list_is_newest_first_with_a_working_cursor(client: AsyncClient) -> None:
	for i in range(5):
		await client.post(
			"/v1/captures", json={"text": f"note {i}", "client_token": f"tok-page-{i}"}
		)

	page1 = (await client.get("/v1/captures", params={"limit": 2})).json()
	assert [c["text"] for c in page1["items"]] == ["note 4", "note 3"]
	assert page1["next_cursor"]

	page2 = (
		await client.get(
			"/v1/captures", params={"limit": 2, "cursor": page1["next_cursor"]}
		)
	).json()
	assert [c["text"] for c in page2["items"]] == ["note 2", "note 1"]

	page3 = (
		await client.get(
			"/v1/captures", params={"limit": 2, "cursor": page2["next_cursor"]}
		)
	).json()
	assert [c["text"] for c in page3["items"]] == ["note 0"]
	assert page3["next_cursor"] is None


async def test_garbage_cursor_is_a_400(client: AsyncClient) -> None:
	assert (
		await client.get("/v1/captures", params={"cursor": "not-a-cursor"})
	).status_code == 400


async def test_unknown_capture_is_a_404(client: AsyncClient) -> None:
	assert (await client.get(f"/v1/captures/{uuid.uuid4()}")).status_code == 404
