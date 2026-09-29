"""Migrations run for real against rows, where a schema-only cycle on an empty database
would pass: 0014 changes production rows, and 0008's downgrade once failed on data."""

import asyncio
import uuid

from alembic.config import Config
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from alembic import command
from memo.config import SEED_USER_ID
from memo.schemas.derivations import Extraction, TodoPayload
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

NO_WORDS = "extract: nothing to extract: the capture has no text"
REAL = "transcribe: deepgram 500"


async def test_0014_readies_only_notes_that_failed_for_having_no_words(
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


async def test_0008_downgrades_with_a_cited_item_whose_note_was_deleted(
	app: FastAPI, client: AsyncClient, admin_engine: AsyncEngine
) -> None:
	"""A note is deleted but its item is kept (ADR 0022), so the item's chunk has no
	capture; an answer then cites that chunk. 0008's downgrade deleted such chunks after
	dropping the cascading key, then re-added a plain key the citation violated."""
	async with app.state.session_factory() as session:
		ctx = PipelineContext(
			session=session,
			transcriber=FakeTranscriber(),
			object_store=FakeObjectStore(),
			extractor=FakeExtractor(
				Extraction(todos=[TodoPayload(title="Call Tunde")])
			),
			embedder=FakeEmbedder(),
			people=FakePeopleReader(),
		)
		created = await client.post(
			"/v1/captures", json={"text": "call Tunde", "client_token": "tok-m08-cited"}
		)
		capture_id = created.json()["id"]
		await process_capture(ctx, uuid.UUID(capture_id), SEED_USER_ID)
	await client.delete(f"/v1/captures/{capture_id}")
	conversation = (await client.post("/v1/conversations")).json()["id"]
	asked = (
		await client.post(
			f"/v1/conversations/{conversation}/messages", json={"text": "Tunde"}
		)
	).json()
	async with client.stream("GET", asked["stream_url"]) as response:
		await response.aread()
	async with admin_engine.connect() as conn:
		orphan_citations = await conn.scalar(
			text(
				"SELECT count(*) FROM message_citations c JOIN chunks k "
				"ON k.id = c.chunk_id WHERE k.capture_id IS NULL"
			)
		)
	assert orphan_citations, "the setup must reproduce the failing state"

	config = Config("alembic.ini")
	try:
		await asyncio.to_thread(command.downgrade, config, "0007")
	finally:
		await asyncio.to_thread(command.upgrade, config, "head")
