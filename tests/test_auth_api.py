"""Sign in with Apple issues a session; sessions gate every route (ADR 0019)."""

import datetime as dt
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID


async def _anon(app: FastAPI) -> AsyncClient:
	return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_first_apple_login_claims_the_seeded_user(app: FastAPI) -> None:
	async with await _anon(app) as anon:
		res = await anon.post(
			"/v1/auth/apple",
			json={
				"identity_token": "apple:sub-1:akin@example.com",
				"device_name": "iPhone",
			},
		)
		assert res.status_code == 200, res.text
		body = res.json()
		assert body["user"]["id"] == str(SEED_USER_ID)
		assert body["token"]

		me = await anon.get(
			"/v1/me", headers={"Authorization": f"Bearer {body['token']}"}
		)
		assert me.status_code == 200
		assert me.json()["id"] == str(SEED_USER_ID)


async def test_a_second_apple_identity_gets_its_own_user(
	app: FastAPI, admin_engine: AsyncEngine
) -> None:
	async with await _anon(app) as anon:
		first = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-1"})
		).json()
		second = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-2"})
		).json()
		assert first["user"]["id"] == str(SEED_USER_ID)
		assert second["user"]["id"] != str(SEED_USER_ID)
		# Signing in again with the same identity returns the same user, not a third.
		again = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-2"})
		).json()
		assert again["user"]["id"] == second["user"]["id"]
	async with admin_engine.connect() as conn:
		count = await conn.scalar(text("SELECT count(*) FROM users"))
	assert count == 2


async def test_a_rejected_identity_token_is_a_401(app: FastAPI) -> None:
	async with await _anon(app) as anon:
		res = await anon.post("/v1/auth/apple", json={"identity_token": "garbage"})
		assert res.status_code == 401


async def test_missing_unknown_and_revoked_sessions_are_401(app: FastAPI) -> None:
	async with await _anon(app) as anon:
		assert (await anon.get("/v1/captures")).status_code == 401
		assert (
			await anon.get(
				"/v1/captures", headers={"Authorization": "Bearer not-a-session"}
			)
		).status_code == 401

		token = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-1"})
		).json()["token"]
		auth = {"Authorization": f"Bearer {token}"}
		assert (await anon.get("/v1/captures", headers=auth)).status_code == 200

		assert (await anon.post("/v1/auth/logout", headers=auth)).status_code == 204
		assert (await anon.get("/v1/captures", headers=auth)).status_code == 401


async def test_expired_sessions_are_401(
	app: FastAPI, admin_engine: AsyncEngine
) -> None:
	async with await _anon(app) as anon:
		token = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-1"})
		).json()["token"]
		auth = {"Authorization": f"Bearer {token}"}
		async with admin_engine.begin() as conn:
			await conn.execute(
				text("UPDATE sessions SET expires_at = :past"),
				{"past": dt.datetime.now(dt.UTC) - dt.timedelta(seconds=1)},
			)
		assert (await anon.get("/v1/captures", headers=auth)).status_code == 401


async def test_logout_only_revokes_this_device(app: FastAPI) -> None:
	async with await _anon(app) as anon:
		phone = (
			await anon.post(
				"/v1/auth/apple",
				json={"identity_token": "apple:sub-1", "device_name": "phone"},
			)
		).json()["token"]
		ipad = (
			await anon.post(
				"/v1/auth/apple",
				json={"identity_token": "apple:sub-1", "device_name": "ipad"},
			)
		).json()["token"]
		await anon.post("/v1/auth/logout", headers={"Authorization": f"Bearer {phone}"})
		assert (
			await anon.get("/v1/me", headers={"Authorization": f"Bearer {ipad}"})
		).status_code == 200


async def test_the_token_is_stored_only_as_a_hash(
	app: FastAPI, admin_engine: AsyncEngine
) -> None:
	async with await _anon(app) as anon:
		token = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-1"})
		).json()["token"]
	async with admin_engine.connect() as conn:
		stored = (await conn.execute(text("SELECT token_hash FROM sessions"))).all()
	assert len(stored) == 1
	assert stored[0][0] != token
	assert len(stored[0][0]) == 64


async def test_two_users_cannot_see_each_others_captures_via_real_sessions(
	app: FastAPI,
) -> None:
	async with await _anon(app) as anon:
		a = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-a"})
		).json()["token"]
		b = (
			await anon.post("/v1/auth/apple", json={"identity_token": "apple:sub-b"})
		).json()["token"]
		created = await anon.post(
			"/v1/captures",
			json={"text": "a's secret", "client_token": f"tok-{uuid.uuid4()}"},
			headers={"Authorization": f"Bearer {a}"},
		)
		assert created.status_code == 202
		cid = created.json()["id"]
		seen_by_b = await anon.get(
			f"/v1/captures/{cid}", headers={"Authorization": f"Bearer {b}"}
		)
		assert seen_by_b.status_code == 404
		listed_by_b = await anon.get(
			"/v1/captures", headers={"Authorization": f"Bearer {b}"}
		)
		assert listed_by_b.json()["items"] == []
