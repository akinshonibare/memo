"""Create-then-stream ask: citations before tokens, persistence, replay, rewrite,
failure, disconnect, and the tenant boundary on the one path that speaks in prose."""

import datetime as dt
import json
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.models import Message
from memo.schemas.derivations import Extraction, TodoPayload
from memo.services.ask import AskContext, run_turn
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retry import TerminalError
from tests.fakes import (
	FakeAnswerProvider,
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeRewriteProvider,
	FakeTranscriber,
)

CAPTURES = [
	"need to call Tunde about the pricing thing before Thursday",
	"felt anxious about work all day, could not focus",
	"dentist moved to the 2nd of October at half two",
]


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


@pytest.fixture
async def corpus(client: AsyncClient, session: AsyncSession) -> list[uuid.UUID]:
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)
	ids = []
	for i, t in enumerate(CAPTURES):
		created = await client.post(
			"/v1/captures", json={"text": t, "client_token": f"tok-ask-{i}"}
		)
		capture_id = uuid.UUID(created.json()["id"])
		assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"
		ids.append(capture_id)
	return ids


async def _events(client: AsyncClient, url: str) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
	events: list[tuple[str, dict]] = []  # type: ignore[type-arg]
	name = None
	async with client.stream("GET", url) as response:
		assert response.status_code == 200, await response.aread()
		assert response.headers["content-type"].startswith("text/event-stream")
		async for line in response.aiter_lines():
			if line.startswith("event: "):
				name = line[len("event: ") :]
			elif line.startswith("data: ") and name is not None:
				events.append((name, json.loads(line[len("data: ") :])))
				name = None
	return events


async def _ask(
	client: AsyncClient, question: str, conversation_id: str | None = None
) -> dict:  # type: ignore[type-arg]
	if conversation_id is None:
		conversation_id = (await client.post("/v1/conversations")).json()["id"]
	accepted = await client.post(
		f"/v1/conversations/{conversation_id}/messages", json={"text": question}
	)
	assert accepted.status_code == 201, accepted.text
	body: dict = accepted.json()  # type: ignore[type-arg]
	return body


async def test_retrieval_arrives_before_the_first_token_and_everything_persists(
	client: AsyncClient, corpus: list[uuid.UUID], admin_engine: AsyncEngine
) -> None:
	accepted = await _ask(client, "what did I need to talk to Tunde about?")
	assert accepted["stream_url"] == f"/v1/messages/{accepted['message_id']}/stream"

	events = await _events(client, accepted["stream_url"])

	names = [n for n, _ in events]
	assert names[0] == "retrieval"
	assert names[-1] == "done"
	assert names.count("token") == 5
	citations = events[0][1]["citations"]
	assert citations and citations[0]["capture_id"] == str(corpus[0])
	assert citations[0]["rank"] == 1 and "Tunde" in citations[0]["snippet"]
	done = events[-1][1]
	assert done["message_id"] == accepted["message_id"]
	assert float(done["usage"]["cost_usd"]) > 0

	history = (
		await client.get(f"/v1/conversations/{accepted['conversation_id']}/messages")
	).json()
	assert [m["role"] for m in history] == ["user", "assistant"]
	answer = history[1]
	assert answer["status"] == "done"
	assert answer["text"] == "Your notes say [1] call Tunde."
	assert answer["ttft_ms"] is not None
	assert [c["rank"] for c in answer["citations"]] == list(
		range(1, len(citations) + 1)
	)
	async with admin_engine.connect() as conn:
		calls: list[str] = list(
			(
				await conn.execute(
					text("SELECT stage FROM llm_calls WHERE message_id = :id"),
					{"id": accepted["message_id"]},
				)
			)
			.scalars()
			.all()
		)
	assert calls == ["answer"]
	conversations = (await client.get("/v1/conversations")).json()["items"]
	assert conversations[0]["title"].startswith("what did I need")


async def test_a_finished_answer_replays_the_same_events(
	client: AsyncClient, corpus: list[uuid.UUID], app: FastAPI
) -> None:
	accepted = await _ask(client, "when is the dentist?")
	first = await _events(client, accepted["stream_url"])
	provider: FakeAnswerProvider = app.state.answer_provider

	second = await _events(client, accepted["stream_url"])

	assert provider.calls == 1, "a replay never re-pays for generation"
	assert [n for n, _ in second] == ["retrieval", "token", "done"]
	assert second[0][1]["citations"] == first[0][1]["citations"]
	assert second[1][1]["delta"] == "".join(
		d["delta"] for n, d in first if n == "token"
	)


async def test_rewrite_is_skipped_on_turn_one_and_used_on_a_follow_up(
	client: AsyncClient, corpus: list[uuid.UUID], app: FastAPI
) -> None:
	rewriter: FakeRewriteProvider = app.state.rewrite_provider
	rewriter.rewritten = "Tunde pricing call"

	first = await _ask(client, "when is the dentist?")
	await _events(client, first["stream_url"])
	assert rewriter.calls == 0

	follow_up = await _ask(client, "and what about him?", first["conversation_id"])
	events = await _events(client, follow_up["stream_url"])

	assert rewriter.calls == 1
	assert events[0][1]["citations"][0]["capture_id"] == str(corpus[0])
	history = (
		await client.get(f"/v1/conversations/{first['conversation_id']}/messages")
	).json()
	assert history[-1]["rewritten_query"] == "Tunde pricing call"
	assert history[-1]["rewrite_ms"] is not None
	provider: FakeAnswerProvider = app.state.answer_provider
	roles = [m["role"] for m in provider.last_messages]
	assert roles == ["user", "assistant", "user"], "earlier turns are in the prompt"


async def test_provider_failure_is_an_error_event_and_a_failed_row(
	client: AsyncClient, corpus: list[uuid.UUID], app: FastAPI
) -> None:
	app.state.answer_provider = FakeAnswerProvider(fail_with=TerminalError("boom"))
	accepted = await _ask(client, "anything")

	events = await _events(client, accepted["stream_url"])

	assert [n for n, _ in events] == ["retrieval", "error"]
	assert events[-1][1]["retryable"] is False
	history = (
		await client.get(f"/v1/conversations/{accepted['conversation_id']}/messages")
	).json()
	assert history[-1]["status"] == "failed" and "boom" in history[-1]["error"]


async def test_client_disconnect_closes_the_upstream_stream_and_keeps_the_partial(
	client: AsyncClient,
	corpus: list[uuid.UUID],
	app: FastAPI,
	admin_engine: AsyncEngine,
) -> None:
	accepted = await _ask(client, "what did I need to talk to Tunde about?")
	provider = FakeAnswerProvider(["one ", "two ", "three ", "four"])
	async with app.state.session_factory() as session:
		await bind_user(session, SEED_USER_ID)
		assistant = await session.get(Message, uuid.UUID(accepted["message_id"]))
		assert assistant is not None
		ctx = AskContext(
			session=session,
			embedder=FakeEmbedder(),
			answer_provider=provider,
			rewrite_provider=FakeRewriteProvider(),
		)
		turn: AsyncGenerator = run_turn(ctx, assistant)  # type: ignore[type-arg]
		seen = []
		async for event in turn:
			seen.append(event.name)
			if seen.count("token") == 2:
				break
		await turn.aclose()  # what Starlette does when the client goes away

	assert provider.closed_early is True
	async with admin_engine.connect() as conn:
		status_, text_, error = (
			await conn.execute(
				text("SELECT status, text, error FROM messages WHERE id = :id"),
				{"id": accepted["message_id"]},
			)
		).one()
	assert status_ == "failed"
	assert text_ == "one two "
	assert "client disconnected after 2 tokens" == error


async def test_streaming_twice_at_once_is_a_409(
	client: AsyncClient, corpus: list[uuid.UUID], admin_engine: AsyncEngine
) -> None:
	accepted = await _ask(client, "anything")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text("UPDATE messages SET status = 'streaming' WHERE id = :id"),
			{"id": accepted["message_id"]},
		)
	assert (await client.get(accepted["stream_url"])).status_code == 409


async def test_another_user_cannot_read_or_stream_the_conversation(
	client: AsyncClient,
	corpus: list[uuid.UUID],
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	accepted = await _ask(client, "what did I need to talk to Tunde about?")
	as_user(second_user)
	assert (await client.get(accepted["stream_url"])).status_code == 404
	assert (
		await client.get(f"/v1/conversations/{accepted['conversation_id']}/messages")
	).status_code == 404
	assert (await client.get("/v1/conversations")).json()["items"] == []


async def test_the_answer_sees_live_derivations_and_today(
	app: FastAPI,
	client: AsyncClient,
	corpus: list[uuid.UUID],
	admin_engine: AsyncEngine,
) -> None:
	# ADR 0021: the handoff's "next Tuesday" hedge. The note says a relative date;
	# the confirmed event says the real one, and the model has to be shown it.
	dentist = corpus[2]
	added = await client.post(
		f"/v1/captures/{dentist}/derivations",
		json={
			"kind": "event",
			"payload": {"title": "Dentist", "starts_at": "2026-10-02T14:30:00+01:00"},
		},
	)
	dropped = await client.post(
		f"/v1/captures/{dentist}/derivations",
		json={"kind": "todo", "payload": {"title": "Wrong reading"}},
	)
	await client.patch(
		f"/v1/derivations/{dropped.json()['id']}", json={"status": "rejected"}
	)
	assert added.status_code == 201

	accepted = await _ask(client, "when is the dentist?")
	await _events(client, accepted["stream_url"])

	provider: FakeAnswerProvider = app.state.answer_provider
	prompt = str(provider.last_messages[-1]["content"])
	assert prompt.startswith("Today is ")
	assert "event: Dentist · Fri 2 Oct 2026, 13:30 (added by you)" in prompt
	assert "Wrong reading" not in prompt


async def test_rejecting_a_derivation_retires_its_chunk(
	client: AsyncClient, admin_engine: AsyncEngine, session: AsyncSession
) -> None:
	extraction = Extraction(
		todos=[TodoPayload(title="Call Tunde", due=dt.date(2026, 10, 1))]
	)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(extraction),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)
	created = await client.post(
		"/v1/captures",
		json={"text": "call Tunde by Thursday", "client_token": "tok-reject-chunk"},
	)
	assert created.status_code == 202, created.text
	capture_id = uuid.UUID(created.json()["id"])
	assert (await process_capture(ctx, capture_id, SEED_USER_ID)).status == "ready"
	async with admin_engine.connect() as conn:
		derivation_id, chunk_text = (
			await conn.execute(
				text(
					"SELECT derivation_id, text FROM chunks WHERE capture_id = :c "
					"AND derivation_id IS NOT NULL"
				),
				{"c": capture_id},
			)
		).one()
	assert chunk_text == "todo: Call Tunde. due 2026-10-01"

	await client.patch(f"/v1/derivations/{derivation_id}", json={"status": "rejected"})

	async with admin_engine.connect() as conn:
		live = await conn.scalar(
			text(
				"SELECT count(*) FROM chunks WHERE derivation_id = :d "
				"AND superseded_at IS NULL"
			),
			{"d": derivation_id},
		)
	assert live == 0
