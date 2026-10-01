"""The operator's cost report (ADR 0063): every user's AI spend, read-only."""

import uuid
from decimal import Decimal

from fastapi import FastAPI
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.admin.costs import render, stage_costs, user_costs
from memo.config import SEED_USER_ID
from memo.db import bind_system


async def _call(admin: AsyncEngine, user: uuid.UUID, stage: str, usd: str) -> None:
	async with admin.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO llm_calls (id, user_id, stage, provider, model, "
				"input_tokens, output_tokens, latency_ms, cost_usd) VALUES "
				"(:id, :u, :s, 'anthropic', 'claude-opus-5', 1, 1, 1, :c)"
			),
			{"id": uuid.uuid4(), "u": user, "s": stage, "c": usd},
		)


async def test_every_users_spend_today_and_over_the_window(
	app: FastAPI, admin_engine: AsyncEngine, second_user: uuid.UUID
) -> None:
	await _call(admin_engine, SEED_USER_ID, "extract", "0.40")
	await _call(admin_engine, SEED_USER_ID, "answer", "0.35")
	await _call(admin_engine, second_user, "extract", "0.05")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("UPDATE users SET daily_ai_limit_usd = 0.50 WHERE id = :u"),
			{"u": SEED_USER_ID},
		)

	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		users = await user_costs(s, days=7)
		stages = await stage_costs(s, days=7)
	assert [(u.user_id, u.window, u.calls, u.over) for u in users] == [
		(SEED_USER_ID, Decimal("0.75"), 2, True),
		(second_user, Decimal("0.05"), 1, False),
	]
	assert [(st.stage, st.calls) for st in stages] == [("extract", 2), ("answer", 1)]

	report = render(users, stages, days=7)
	assert "AT LIMIT" in report and "$0.80" in report  # the total
	assert "2 users, 1 at their limit now" in report
	assert "By stage, last 7 days" in report


async def test_the_snapshot_data_names_nobody_by_email(
	app: FastAPI, admin_engine: AsyncEngine
) -> None:
	import datetime as dt
	import json

	from memo.admin.costs import as_json, daily_totals

	async with admin_engine.begin() as conn:
		await conn.execute(
			text("UPDATE users SET email = 'akin@example.com' WHERE id = :u"),
			{"u": SEED_USER_ID},
		)
	await _call(admin_engine, SEED_USER_ID, "extract", "0.30")
	now = dt.datetime.now(dt.UTC)
	async with app.state.session_factory() as s, s.begin():
		await bind_system(s)
		users = await user_costs(s, days=7, now=now)
		stages = await stage_costs(s, days=7, now=now)
		daily = await daily_totals(s, days=14, now=now)
	data = json.loads(as_json(users, stages, daily, days=7, now=now))

	assert "example.com" not in json.dumps(data)
	assert data["users"][0]["who"] == f"user {str(SEED_USER_ID)[:8]}"
	assert len(data["daily"]) == 14 and data["daily"][-1]["usd"] == "0.30"
	assert data["daily"][0]["usd"] == "0.00", "quiet days are kept, as zeros"
