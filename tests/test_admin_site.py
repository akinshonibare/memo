"""The operator's live cost page (ADR 0064): behind a password, read-only, live."""

import base64
import json
import uuid

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID, Settings, get_settings

TOKEN = "op-" + "x" * 40


def _basic(password: str) -> dict[str, str]:
	raw = base64.b64encode(f"akin:{password}".encode()).decode()
	return {"Authorization": f"Basic {raw}"}


def _with_token(app: FastAPI, settings: Settings) -> None:
	configured = settings.model_copy(update={"operator_token": SecretStr(TOKEN)})
	app.dependency_overrides[get_settings] = lambda: configured


def _anon(app: FastAPI) -> AsyncClient:
	return AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


async def test_without_a_token_configured_there_is_no_page(app: FastAPI) -> None:
	async with _anon(app) as anon:
		assert (
			await anon.get("/admin/costs", headers=_basic(TOKEN))
		).status_code == 404


async def test_the_page_asks_for_the_password_and_counts_wrong_ones(
	app: FastAPI, settings: Settings
) -> None:
	_with_token(app, settings)
	async with _anon(app) as anon:
		bare = await anon.get("/admin/costs")
		assert bare.status_code == 401
		assert bare.headers["www-authenticate"].startswith("Basic")
		# A user's own session is not the operator's password.
		assert (
			await anon.get("/admin/costs", headers={"Authorization": "Bearer abc"})
		).status_code == 401
		codes = [
			(await anon.get("/admin/costs.json", headers=_basic("guess"))).status_code
			for _ in range(11)
		]
		assert codes[:10] == [401] * 10 and codes[10] == 429


async def test_the_operator_sees_every_users_spend_live(
	app: FastAPI, settings: Settings, admin_engine: AsyncEngine
) -> None:
	_with_token(app, settings)
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("UPDATE users SET display_name = :n WHERE id = :u"),
			{"n": "Akin </script><script>alert(1)</script>", "u": SEED_USER_ID},
		)
		await conn.execute(
			text(
				"INSERT INTO llm_calls (id, user_id, stage, provider, model, "
				"input_tokens, output_tokens, latency_ms, cost_usd) VALUES "
				"(:id, :u, 'extract', 'anthropic', 'claude-opus-5', 1, 1, 1, 0.42)"
			),
			{"id": uuid.uuid4(), "u": SEED_USER_ID},
		)
	async with _anon(app) as anon:
		data = (await anon.get("/admin/costs.json", headers=_basic(TOKEN))).json()
		page = await anon.get("/admin/costs", headers=_basic(TOKEN))

	assert data["users"][0]["today_usd"] == "0.42"
	assert data["stages"] == [
		{"stage": "extract", "provider": "anthropic", "calls": 1, "usd": "0.42"}
	]
	assert page.status_code == 200
	assert page.headers["cache-control"] == "no-store"
	assert "noindex" in page.headers["x-robots-tag"]
	assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
	# The data is in the page, and a name can't close its script.
	assert '"0.42"' in page.text
	assert "</script><script>alert(1)" not in page.text
	assert json.loads(
		page.text.split("const DATA = ", 1)[1].split(";\n", 1)[0].replace("<\\/", "</")
	)["users"][0]["who"].startswith("Akin </script>")
