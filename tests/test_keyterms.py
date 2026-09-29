"""People's names sent to Deepgram as keyterms (ADR 0013, ADR 0045): which names,
in what order, and that every way audio reaches Deepgram carries them."""

import uuid

import httpx
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.services.people import keyterms
from memo.services.pipeline import process_capture
from memo.services.transcription import DeepgramTranscriber
from tests.fakes import FakeObjectStore, FakeTranscriber
from tests.test_pipeline import _audio_capture, _ctx, session, store  # noqa: F401

_OK = {"results": {"channels": [{"alternatives": [{"transcript": "hi", "words": []}]}]}}


async def _people(
	engine: AsyncEngine, *rows: tuple[str, list[str], int | None]
) -> None:
	"""(name, aliases, days since last mentioned or None) per person."""
	async with engine.begin() as conn:
		for name, aliases, days in rows:
			pid = uuid.uuid4()
			await conn.execute(
				text(
					"INSERT INTO people (id, user_id, name, aliases) "
					"VALUES (:id, :u, :n, :a)"
				),
				{"id": pid, "u": SEED_USER_ID, "n": name, "a": aliases},
			)
			if days is not None:
				await conn.execute(
					text(
						"INSERT INTO derivations (id, user_id, kind, payload, status, "
						"extractor_version, person_id, created_at) VALUES (:id, :u, "
						"'mention', :p, 'confirmed', 'test', :pid, "
						"now() - make_interval(days => :d))"
					),
					{
						"id": uuid.uuid4(),
						"u": SEED_USER_ID,
						"p": f'{{"name": "{name}"}}',
						"pid": pid,
						"d": days,
					},
				)


async def test_names_most_recent_first_without_kin_words(
	session: AsyncSession,  # noqa: F811
	admin_engine: AsyncEngine,
) -> None:
	await _people(
		admin_engine,
		("Tunde Bakare", ["Tunde B"], 30),
		("Folake Shonibare", ["Mum", "mummy"], 1),
		("Adaeze", [], None),
	)
	await bind_user(session, SEED_USER_ID)

	terms = await keyterms(session, SEED_USER_ID)

	assert terms == ["Folake Shonibare", "Tunde Bakare", "Tunde B", "Adaeze"]
	assert await keyterms(session, SEED_USER_ID, limit=2) == terms[:2]


async def test_a_voice_note_is_transcribed_listening_for_the_names(
	client: AsyncClient,
	admin_engine: AsyncEngine,
	session: AsyncSession,  # noqa: F811
	store: FakeObjectStore,  # noqa: F811
) -> None:
	await _people(admin_engine, ("Tunde Bakare", [], 2))
	capture_id = await _audio_capture(client, admin_engine, store, "tok-keyterms")
	transcriber = FakeTranscriber("called Tunde")

	await process_capture(
		_ctx(session, store, transcriber=transcriber), capture_id, SEED_USER_ID
	)

	assert transcriber.keyterms == ["Tunde Bakare"]


async def test_each_keyterm_is_its_own_parameter_on_nova_3_only() -> None:
	seen: list[list[str]] = []

	def handler(request: httpx.Request) -> httpx.Response:
		seen.append(request.url.params.get_list("keyterm"))
		return httpx.Response(200, json=_OK)

	for model in ("nova-3", "nova-2"):
		transcriber = DeepgramTranscriber(
			api_key="k",
			http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
			model=model,
		)
		await transcriber.transcribe(
			b"x", "audio/m4a", keyterms=["Tunde Bakare", "Adaeze"]
		)

	assert seen == [["Tunde Bakare", "Adaeze"], []]


async def test_hold_to_talk_listens_for_the_names_too(
	client: AsyncClient, app: FastAPI, admin_engine: AsyncEngine
) -> None:
	await _people(admin_engine, ("Tunde Bakare", [], 2))
	transcriber = FakeTranscriber("when did I last see Tunde")
	app.state.transcriber = transcriber

	response = await client.post(
		"/v1/voice/transcribe",
		content=b"audio",
		headers={"content-type": "audio/m4a"},
	)

	assert response.status_code == 200, response.text
	assert transcriber.keyterms == ["Tunde Bakare"]
