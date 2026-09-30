"""How memo writes (ADR 0061): a tone per person, and no em dashes for anyone."""

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.prompts import answer_v2, describe_v1, extract_v1
from memo.schemas.derivations import Extraction, IdeaPayload, TodoPayload
from memo.services.pipeline import PipelineContext, process_capture
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

AT = dt.datetime(2026, 9, 30, 9, 0, tzinfo=dt.UTC)


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def test_the_tone_is_plain_until_you_choose_and_only_the_four(
	client: AsyncClient,
) -> None:
	assert (await client.get("/v1/me")).json()["tone"] == "plain"
	chose = await client.patch("/v1/me", json={"tone": "playful"})
	assert chose.status_code == 200 and chose.json()["tone"] == "playful"
	assert (await client.get("/v1/me")).json()["tone"] == "playful"
	assert (await client.patch("/v1/me", json={"tone": "snarky"})).status_code == 422


def test_every_prose_prompt_asks_for_no_em_dashes_and_carries_the_tone() -> None:
	for tone, word in (("plain", "Plain:"), ("brief", "Brief:"), ("playful", "never")):
		assert word.lower() in answer_v2.system(tone).lower()
		assert word.lower() in describe_v1.user_message(None, tone).lower()
		assert (
			word.lower()
			in extract_v1.user_message(
				"x", captured_at=AT, timezone="UTC", tone=tone
			).lower()
		)
	for prompt in (
		answer_v2.system("warm"),
		extract_v1.SYSTEM_PROMPT,
		describe_v1.SYSTEM_PROMPT,
	):
		assert "Never use em dashes" in prompt
		assert "—" not in prompt, "a prompt that bans them shouldn't use one"
	# An unknown stored tone is plain, never an error.
	assert "Plain:" in answer_v2.system("snarky")


async def test_a_note_is_read_in_your_tone_and_what_memo_writes_has_no_em_dashes(
	client: AsyncClient, session: AsyncSession
) -> None:
	await client.patch("/v1/me", json={"tone": "warm"})
	extractor = FakeExtractor(
		Extraction(
			todos=[TodoPayload(title="Call Tunde — pricing")],
			ideas=[
				IdeaPayload(
					title="Weekly, not daily",
					summary="Resurface an idea weekly — daily is too much.",
				)
			],
		)
	)
	made = await client.post(
		"/v1/captures",
		json={"text": "call tunde — pricing", "client_token": f"t-{uuid.uuid4()}"},
	)
	cid = uuid.UUID(made.json()["id"])
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=extractor,
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)
	assert (await process_capture(ctx, cid, SEED_USER_ID)).status == "ready"
	assert extractor.tones == ["warm"]

	note = (await client.get(f"/v1/captures/{cid}")).json()
	# Your own words keep their dash; what memo wrote doesn't.
	assert note["text"] == "call tunde — pricing"
	written = {d["kind"]: d["payload"] for d in note["derivations"]}
	assert written["todo"]["title"] == "Call Tunde, pricing"
	assert written["idea"]["summary"] == "Resurface an idea weekly, daily is too much."
