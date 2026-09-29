"""Migration 0014 changes rows in production, so it is run for real here: step back to
0013, put one note that failed only for having no words next to one that failed for a
real reason, step forward, and check that only the first changed."""

import asyncio

from alembic.config import Config
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command

NO_WORDS = "extract: nothing to extract: the capture has no text"
REAL = "transcribe: deepgram 500"


async def test_only_notes_that_failed_for_having_no_words_become_ready(
	client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	ids = []
	for n in range(3):
		created = await client.post(
			"/v1/captures", json={"text": f"note {n}", "client_token": f"tok-m14-{n}"}
		)
		ids.append(created.json()["id"])
	silent, broken, worded = ids
	config = Config("alembic.ini")
	# env.py calls asyncio.run, so alembic runs off this event loop.
	await asyncio.to_thread(command.downgrade, config, "0013")
	try:
		async with admin_engine.begin() as conn:
			for capture_id, body, reason in (
				(silent, "  ", NO_WORDS),
				(broken, "", REAL),
				(worded, "has words", NO_WORDS),  # not ours to second-guess
			):
				await conn.execute(
					text(
						"UPDATE captures SET status = 'failed', text = :t, "
						"failure_reason = :r WHERE id = :id"
					),
					{"t": body, "r": reason, "id": capture_id},
				)
	finally:
		await asyncio.to_thread(command.upgrade, config, "head")

	async with admin_engine.connect() as conn:
		result = await conn.execute(
			text("SELECT id::text, status FROM captures WHERE id::text = ANY(:ids)"),
			{"ids": ids},
		)
		rows: dict[str, str] = {r[0]: r[1] for r in result.all()}
	assert rows == {silent: "ready", broken: "failed", worded: "failed"}
