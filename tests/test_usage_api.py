import uuid

from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from memo.config import SEED_USER_ID


async def _call(
	admin_engine: AsyncEngine, user_id: uuid.UUID, stage: str, cost: str, days_ago: int
) -> None:
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"INSERT INTO llm_calls (id, user_id, stage, provider, model, "
				"input_tokens, output_tokens, latency_ms, cost_usd, created_at) VALUES "
				"(:id, :user_id, :stage, 'anthropic', 'claude-opus-5', 1000, 100, 900, "
				":cost, now() - make_interval(days => :days))"
			),
			{
				"id": uuid.uuid4(),
				"user_id": user_id,
				"stage": stage,
				"cost": cost,
				"days": days_ago,
			},
		)


async def test_usage_answers_what_last_week_cost_per_stage(
	client: AsyncClient, admin_engine: AsyncEngine, second_user: uuid.UUID
) -> None:
	await _call(admin_engine, SEED_USER_ID, "extract", "0.0075", 1)
	await _call(admin_engine, SEED_USER_ID, "extract", "0.0075", 2)
	await _call(admin_engine, SEED_USER_ID, "answer", "0.0200", 3)
	await _call(admin_engine, SEED_USER_ID, "extract", "9.9999", 30)  # outside window
	await _call(admin_engine, second_user, "extract", "5.0000", 1)  # someone else

	usage = (await client.get("/v1/usage", params={"days": 7})).json()

	assert usage["window_days"] == 7
	assert float(usage["total_cost_usd"]) == 0.035
	by_stage = {row["stage"]: row for row in usage["by_stage"]}
	assert by_stage["extract"]["calls"] == 2
	assert by_stage["extract"]["input_tokens"] == 2000
	assert float(by_stage["extract"]["cost_usd"]) == 0.015
	assert by_stage["answer"]["calls"] == 1
