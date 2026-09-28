"""Deleting a note removes everything it produced; playback comes from the bucket."""

import uuid

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.fakes import FakeObjectStore


@pytest.fixture
def store(app: FastAPI) -> FakeObjectStore:
	fake = FakeObjectStore()
	app.state.object_store = fake
	return fake


async def _audio_capture(client: AsyncClient, store: FakeObjectStore) -> dict[str, str]:
	rec = (
		await client.post(
			"/v1/recordings",
			json={"mime": "audio/m4a", "bytes": 10, "duration_ms": 7_000},
		)
	).json()
	key = rec["upload_url"].split("fake-bucket.test/")[1].split("?")[0]
	store.objects[key] = b"0123456789"
	cap = await client.post(
		"/v1/captures",
		json={
			"recording_id": rec["recording_id"],
			"client_token": f"tok-{uuid.uuid4()}",
		},
	)
	assert cap.status_code == 202, cap.text
	return {"id": cap.json()["id"], "key": key}


async def test_list_and_detail_carry_the_recording_duration(
	client: AsyncClient, store: FakeObjectStore
) -> None:
	made = await _audio_capture(client, store)
	listed = (await client.get("/v1/captures")).json()["items"]
	assert listed[0]["id"] == made["id"]
	assert listed[0]["duration_ms"] == 7_000
	detail = (await client.get(f"/v1/captures/{made['id']}")).json()
	assert detail["duration_ms"] == 7_000

	text_note = (
		await client.post(
			"/v1/captures", json={"text": "hello", "client_token": "tok-text-dur"}
		)
	).json()
	assert text_note["duration_ms"] is None


async def test_audio_url_is_presigned_from_the_bucket(
	client: AsyncClient, store: FakeObjectStore
) -> None:
	made = await _audio_capture(client, store)
	res = await client.get(f"/v1/captures/{made['id']}/audio")
	assert res.status_code == 200, res.text
	body = res.json()
	assert body["url"].startswith("https://fake-bucket.test/")
	assert "?get" in body["url"]
	assert body["mime"] == "audio/m4a"
	assert body["duration_ms"] == 7_000


async def test_text_captures_have_no_audio(client: AsyncClient) -> None:
	note = (
		await client.post(
			"/v1/captures", json={"text": "hello", "client_token": "tok-no-audio"}
		)
	).json()
	assert (await client.get(f"/v1/captures/{note['id']}/audio")).status_code == 404


async def test_delete_removes_the_row_and_the_bytes(
	client: AsyncClient, store: FakeObjectStore, admin_engine: AsyncEngine
) -> None:
	made = await _audio_capture(client, store)
	assert (await client.delete(f"/v1/captures/{made['id']}")).status_code == 204
	assert (await client.get(f"/v1/captures/{made['id']}")).status_code == 404
	assert made["key"] in store.deleted
	async with admin_engine.connect() as conn:
		recordings = await conn.scalar(text("SELECT count(*) FROM recordings"))
		captures = await conn.scalar(text("SELECT count(*) FROM captures"))
	assert (recordings, captures) == (0, 0)


async def test_delete_of_an_unknown_capture_is_a_404(client: AsyncClient) -> None:
	assert (await client.delete(f"/v1/captures/{uuid.uuid4()}")).status_code == 404
