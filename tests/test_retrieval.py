"""Hybrid retrieval against the real Postgres: HNSW, GIN, RRF, and the tenant boundary
on the one path where a leak arrives as fluent prose (ADR 0010)."""

import uuid
from collections.abc import AsyncIterator, Callable

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from memo.config import SEED_USER_ID
from memo.db import bind_user
from memo.services.pipeline import PipelineContext, process_capture
from memo.services.retrieval import lexical_query, rrf, search
from tests.fakes import (
	FakeEmbedder,
	FakeExtractor,
	FakeObjectStore,
	FakePeopleReader,
	FakeTranscriber,
)

CAPTURES = [
	"need to call Tunde about the pricing thing before Thursday",
	"felt anxious about work all day, could not focus on anything",
	"dentist appointment moved to the 2nd of October at half two",
	"idea: resurface one old note every morning instead of a feed",
]


@pytest.fixture
async def session(app: FastAPI) -> AsyncIterator[AsyncSession]:
	async with app.state.session_factory() as s:
		await bind_user(s, SEED_USER_ID)
		yield s


async def _ingest(
	client: AsyncClient, session: AsyncSession, texts: list[str], prefix: str
) -> list[uuid.UUID]:
	ids = []
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)
	for i, t in enumerate(texts):
		created = await client.post(
			"/v1/captures", json={"text": t, "client_token": f"{prefix}-{i}"}
		)
		capture_id = uuid.UUID(created.json()["id"])
		outcome = await process_capture(ctx, capture_id, SEED_USER_ID)
		assert outcome.status == "ready"
		ids.append(capture_id)
	return ids


async def test_rrf_sums_reciprocal_ranks() -> None:
	a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
	scores = rrf([a, b], [b, c])
	assert scores[b] == pytest.approx(1 / 61 + 1 / 62)
	assert scores[a] == pytest.approx(1 / 61)
	assert scores[c] == pytest.approx(1 / 62)
	assert max(scores, key=lambda k: scores[k]) == b


async def test_embed_stage_writes_chunks_with_a_generated_tsvector(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	[capture_id] = await _ingest(client, session, CAPTURES[:1], "tok-ret-a")
	async with admin_engine.connect() as conn:
		rows = (
			await conn.execute(
				text(
					"SELECT ordinal, embedding_model, embedding_dim, "
					"tsv @@ websearch_to_tsquery('english', 'Tunde pricing') AS hit, "
					"superseded_at FROM chunks WHERE capture_id = :id"
				),
				{"id": capture_id},
			)
		).all()
	assert len(rows) == 1
	assert rows[0][1] == "fake-bow" and rows[0][2] == 1024
	assert rows[0][3] is True and rows[0][4] is None


async def test_rerunning_embed_supersedes_rather_than_deletes(
	client: AsyncClient, session: AsyncSession, admin_engine: AsyncEngine
) -> None:
	[capture_id] = await _ingest(client, session, CAPTURES[:1], "tok-ret-b")
	async with admin_engine.begin() as conn:
		await conn.execute(
			text(
				"UPDATE job_runs SET status = 'failed' "
				"WHERE capture_id = :id AND stage = 'embed'"
			),
			{"id": capture_id},
		)
		await conn.execute(
			text("UPDATE captures SET status = 'embedding' WHERE id = :id"),
			{"id": capture_id},
		)
	ctx = PipelineContext(
		session=session,
		transcriber=FakeTranscriber(),
		object_store=FakeObjectStore(),
		extractor=FakeExtractor(),
		embedder=FakeEmbedder(),
		people=FakePeopleReader(),
	)
	await process_capture(ctx, capture_id, SEED_USER_ID)

	async with admin_engine.connect() as conn:
		live, dead = (
			await conn.execute(
				text(
					"SELECT count(*) FILTER (WHERE superseded_at IS NULL), "
					"count(*) FILTER (WHERE superseded_at IS NOT NULL) "
					"FROM chunks WHERE capture_id = :id"
				),
				{"id": capture_id},
			)
		).one()
	assert (live, dead) == (1, 1)


async def test_hybrid_search_finds_a_proper_noun_and_fuses_both_branches(
	client: AsyncClient, session: AsyncSession
) -> None:
	ids = await _ingest(client, session, CAPTURES, "tok-ret-c")

	result = await search(
		session,
		user_id=SEED_USER_ID,
		query="Tunde pricing",
		embedder=FakeEmbedder(),
		k=3,
	)

	assert result.hits[0].capture_id == ids[0]
	assert result.hits[0].keyword_rank == 1
	assert result.hits[0].vector_rank is not None
	assert result.hits[0].score == pytest.approx(
		1 / 61 + 1 / (60 + result.hits[0].vector_rank)
	)
	assert result.keyword_ranked and result.vector_ranked
	assert len({h.capture_id for h in result.hits}) == len(result.hits)


async def test_keyword_branch_is_empty_for_a_paraphrase_but_vector_branch_answers(
	client: AsyncClient, session: AsyncSession
) -> None:
	await _ingest(client, session, CAPTURES, "tok-ret-d")

	result = await search(
		session,
		user_id=SEED_USER_ID,
		query="zzz-no-such-token",
		embedder=FakeEmbedder(),
	)

	assert result.keyword_ranked == []
	assert len(result.vector_ranked) == len(CAPTURES)
	assert len(result.hits) == len(CAPTURES)


async def test_search_never_returns_another_users_chunks(
	client: AsyncClient,
	session: AsyncSession,
	app: FastAPI,
	as_user: Callable[[uuid.UUID], None],
	second_user: uuid.UUID,
) -> None:
	await _ingest(client, session, CAPTURES, "tok-ret-e")

	async with app.state.session_factory() as other:
		await bind_user(other, second_user)
		result = await search(
			other, user_id=second_user, query="Tunde pricing", embedder=FakeEmbedder()
		)
	assert result.hits == []
	assert result.vector_ranked == [] and result.keyword_ranked == []


def test_lexical_query_ors_the_terms() -> None:
	assert lexical_query("what did I need to talk to Tunde about?") == (
		"what OR did OR need OR to OR talk OR to OR Tunde OR about"
	)
	assert lexical_query("???") == "???"


async def test_a_question_still_reaches_the_keyword_branch(
	client: AsyncClient, session: AsyncSession
) -> None:
	ids = await _ingest(client, session, CAPTURES, "tok-ret-f")

	result = await search(
		session,
		user_id=SEED_USER_ID,
		query="what did I need to talk to Tunde about?",
		embedder=FakeEmbedder(),
	)

	assert result.hits[0].capture_id == ids[0]
	assert result.hits[0].keyword_rank == 1
