"""Per-user limits in Redis; fail-closed on ask, fail-open on capture (issue #48)."""

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from memo.config import Settings
from memo.services.ratelimit import check
from tests.fakes import FakeRedis


class BrokenRedis:
	async def incr(self, name: str) -> int:
		raise ConnectionError("redis is down")

	async def expire(self, name: str, time: int) -> bool:
		raise ConnectionError("redis is down")


MemoryRedis = FakeRedis


async def test_the_real_redis_client_counts_and_expires(app: FastAPI) -> None:
	key = f"integration:{uuid.uuid4()}"
	first = await check(
		app.state.real_redis, key=key, limit=1, window_seconds=60, on_error="closed"
	)
	second = await check(
		app.state.real_redis, key=key, limit=1, window_seconds=60, on_error="closed"
	)
	assert first.allowed and not second.allowed
	ttl = await app.state.real_redis.ttl(
		f"ratelimit:{key}:{int(__import__('time').time() // 60)}"
	)
	assert 0 < ttl <= 61


async def test_window_counts_and_resets() -> None:
	redis = MemoryRedis()
	key = f"t:{uuid.uuid4()}"
	decisions = [
		await check(
			redis, key=key, limit=2, window_seconds=60, on_error="closed", now=1000.0
		)
		for _ in range(3)
	]
	assert [d.allowed for d in decisions] == [True, True, False]
	assert decisions[0].remaining == 1 and decisions[1].remaining == 0
	assert 0 < decisions[2].retry_after <= 60
	assert list(redis.ttls.values()) == [61], "expiry set once, on the first hit"

	later = await check(
		redis, key=key, limit=2, window_seconds=60, on_error="closed", now=1061.0
	)
	assert later.allowed


async def test_redis_outage_policy_is_explicit() -> None:
	closed = await check(
		BrokenRedis(), key="a", limit=5, window_seconds=60, on_error="closed"
	)
	opened = await check(
		BrokenRedis(), key="a", limit=5, window_seconds=60, on_error="open"
	)
	assert closed.allowed is False and closed.retry_after >= 1
	assert opened.allowed is True


@pytest.fixture
def tight_limits(app: FastAPI, settings: Settings) -> Settings:
	tight = settings.model_copy(
		update={"rate_ask_per_minute": 2, "rate_capture_per_minute": 3}
	)
	from memo.config import get_settings

	app.dependency_overrides[get_settings] = lambda: tight
	return tight


async def test_ask_returns_429_with_retry_after_and_capture_keeps_going(
	client: AsyncClient, app: FastAPI, tight_limits: Settings
) -> None:
	app.state.redis = MemoryRedis()
	conversation_id = (await client.post("/v1/conversations")).json()["id"]
	codes = [
		(
			await client.post(
				f"/v1/conversations/{conversation_id}/messages", json={"text": "q"}
			)
		).status_code
		for _ in range(3)
	]
	assert codes == [201, 201, 429]
	limited = await client.post(
		f"/v1/conversations/{conversation_id}/messages", json={"text": "q"}
	)
	assert limited.headers["Retry-After"].isdigit()
	assert limited.headers["X-RateLimit-Remaining"] == "0"

	capture_codes = [
		(
			await client.post(
				"/v1/captures", json={"text": "x", "client_token": f"tok-rl-{i}"}
			)
		).status_code
		for i in range(4)
	]
	assert capture_codes == [202, 202, 202, 429]


async def test_when_redis_is_down_ask_fails_closed_and_capture_fails_open(
	client: AsyncClient, app: FastAPI
) -> None:
	app.state.redis = BrokenRedis()
	conversation_id = (await client.post("/v1/conversations")).json()["id"]
	ask = await client.post(
		f"/v1/conversations/{conversation_id}/messages", json={"text": "q"}
	)
	capture = await client.post(
		"/v1/captures", json={"text": "still accepted", "client_token": "tok-rl-open"}
	)
	assert ask.status_code == 429
	assert capture.status_code == 202


async def test_limits_are_per_user(
	client: AsyncClient, app: FastAPI, tight_limits: Settings, second_user: uuid.UUID
) -> None:
	from memo.auth import get_current_user

	app.state.redis = MemoryRedis()
	for i in range(3):
		await client.post(
			"/v1/captures", json={"text": "x", "client_token": f"tok-rlu-a-{i}"}
		)
	blocked = await client.post(
		"/v1/captures", json={"text": "x", "client_token": "tok-rlu-a-3"}
	)
	assert blocked.status_code == 429

	app.dependency_overrides[get_current_user] = lambda: second_user
	other: Any = await client.post(
		"/v1/captures", json={"text": "x", "client_token": "tok-rlu-b-0"}
	)
	assert other.status_code == 202
