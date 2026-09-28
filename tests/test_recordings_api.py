"""Presigned upload straight to the bucket (MinIO here), then attach to a capture."""

import uuid
from collections.abc import Callable

import httpx
from httpx import AsyncClient

from tests.fakes import FakeQueue


async def test_upload_then_capture(client: AsyncClient, queue: FakeQueue) -> None:
	created = await client.post(
		"/v1/recordings", json={"mime": "audio/m4a", "bytes": 10, "duration_ms": 1200}
	)
	assert created.status_code == 201, created.text
	rec = created.json()
	assert rec["upload_url"].startswith("http")

	async with httpx.AsyncClient() as raw:
		put = await raw.put(
			rec["upload_url"],
			content=b"0123456789",
			headers={"Content-Type": "audio/m4a"},
		)
	assert put.status_code == 200, put.text

	cap = await client.post(
		"/v1/captures",
		json={"recording_id": rec["recording_id"], "client_token": "tok-audio-1"},
	)
	assert cap.status_code == 202, cap.text
	assert cap.json()["kind"] == "audio"
	assert cap.json()["recording_id"] == rec["recording_id"]
	assert [j.capture_id for j in queue.jobs] == [uuid.UUID(cap.json()["id"])]


async def test_capture_before_upload_is_a_409(client: AsyncClient) -> None:
	rec = (await client.post("/v1/recordings", json={"mime": "audio/wav"})).json()
	cap = await client.post(
		"/v1/captures",
		json={"recording_id": rec["recording_id"], "client_token": "tok-audio-2"},
	)
	assert cap.status_code == 409


async def test_unsupported_mime_is_a_422(client: AsyncClient) -> None:
	assert (
		await client.post("/v1/recordings", json={"mime": "audio/flac"})
	).status_code == 422
	assert (
		await client.post("/v1/recordings", json={"mime": "video/mp4"})
	).status_code == 422


async def test_another_users_recording_is_a_404(
	client: AsyncClient, as_user: Callable[[uuid.UUID], None], second_user: uuid.UUID
) -> None:
	rec = (await client.post("/v1/recordings", json={"mime": "audio/m4a"})).json()
	as_user(second_user)
	cap = await client.post(
		"/v1/captures",
		json={"recording_id": rec["recording_id"], "client_token": "tok-audio-3"},
	)
	assert cap.status_code == 404


async def test_text_and_recording_are_mutually_exclusive(client: AsyncClient) -> None:
	both = await client.post(
		"/v1/captures",
		json={
			"text": "x",
			"recording_id": str(uuid.uuid4()),
			"client_token": "tok-both",
		},
	)
	neither = await client.post("/v1/captures", json={"client_token": "tok-neither"})
	assert both.status_code == 422
	assert neither.status_code == 422
