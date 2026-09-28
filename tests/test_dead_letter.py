"""Dead-letter is a product state: visible in the API, recoverable from the app."""

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.fakes import FakeQueue


async def test_failed_capture_is_visible_with_a_reason_and_retryable(
	client: AsyncClient, admin_engine: AsyncEngine, queue: FakeQueue
) -> None:
	created = (
		await client.post(
			"/v1/captures", json={"text": "hello", "client_token": "tok-dead-1"}
		)
	).json()
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE captures SET status = 'failed', attempt_count = 5, "
				"failure_reason = 'transcribe: gave up after 5 attempts: HTTP 503' "
				"WHERE id = :id"
			),
			{"id": created["id"]},
		)

	shown = (await client.get(f"/v1/captures/{created['id']}")).json()
	assert shown["status"] == "failed"
	assert "gave up" in shown["failure_reason"]

	queue.jobs.clear()
	retried = await client.post(f"/v1/captures/{created['id']}/retry")
	assert retried.status_code == 202
	assert retried.json()["status"] == "pending"
	assert retried.json()["failure_reason"] is None
	assert retried.json()["attempt_count"] == 0
	assert len(queue.jobs) == 1


async def test_retrying_a_capture_that_did_not_fail_is_a_409(
	client: AsyncClient,
) -> None:
	created = (
		await client.post(
			"/v1/captures", json={"text": "hello", "client_token": "tok-dead-2"}
		)
	).json()
	assert (await client.post(f"/v1/captures/{created['id']}/retry")).status_code == 409
