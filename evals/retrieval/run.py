"""Retrieval eval: recall@5 and recall@10 for vector-only, keyword-only and fused.

	uv run python -m evals.retrieval.run            # Voyage, needs VOYAGE_API_KEY
	uv run python -m evals.retrieval.run --fake     # bag-of-words embedder, no network

Ingests the corpus for a throwaway eval user in the configured database (as the owner,
bypassing the pipeline but using the same chunker and embedder), runs every question
through `search`, and removes the user afterwards. Issue #33's done-when: numbers that
show hybrid beats either branch alone - or evidence that it doesn't.
"""

import argparse
import asyncio
import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from memo.config import get_settings
from memo.db import bind_user, make_session_factory
from memo.ids import uuid7
from memo.services.chunking import chunk_text
from memo.services.embeddings import Embedder, VoyageEmbedder
from memo.services.retrieval import search

HERE = Path(__file__).parent
THRESHOLD_FUSED_RECALL_AT_10 = 0.85


class CorpusItem(BaseModel):
	id: str
	text: str


class Question(BaseModel):
	id: str
	question: str
	expected: list[str]
	note: str | None = None


def _load(path: Path, model: type[BaseModel]) -> list:  # type: ignore[type-arg]
	return [
		model.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


@dataclass
class Recall:
	hits5: int = 0
	hits10: int = 0
	total: int = 0

	def add(
		self, ranked_capture_ids: list[uuid.UUID], expected: set[uuid.UUID]
	) -> None:
		self.total += len(expected)
		self.hits5 += len(expected & set(ranked_capture_ids[:5]))
		self.hits10 += len(expected & set(ranked_capture_ids[:10]))

	@property
	def at5(self) -> float:
		return self.hits5 / self.total if self.total else 1.0

	@property
	def at10(self) -> float:
		return self.hits10 / self.total if self.total else 1.0


async def run(embedder: Embedder) -> dict[str, Recall]:
	settings = get_settings()
	corpus = _load(HERE / "corpus.jsonl", CorpusItem)
	questions = _load(HERE / "questions.jsonl", Question)
	eval_user = uuid7()
	capture_ids: dict[str, uuid.UUID] = {}

	admin = create_async_engine(settings.database_admin_url)
	app_engine = create_async_engine(settings.database_url)
	sessions = make_session_factory(app_engine)
	try:
		async with admin.begin() as conn:
			await conn.execute(
				text("INSERT INTO users (id, timezone) VALUES (:id, 'UTC')"),
				{"id": eval_user},
			)
			texts_by_capture: list[
				tuple[uuid.UUID, list[str], list[tuple[int, int, int]]]
			] = []
			for item in corpus:
				capture_id = uuid7()
				capture_ids[item.id] = capture_id
				await conn.execute(
					text(
						"INSERT INTO captures (id, user_id, kind, text, status, "
						"client_token) "
						"VALUES (:id, :user_id, 'text', :text, 'ready', :tok)"
					),
					{
						"id": capture_id,
						"user_id": eval_user,
						"text": item.text,
						"tok": item.id,
					},
				)
				pieces = chunk_text(item.text)
				texts_by_capture.append(
					(
						capture_id,
						[p.text for p in pieces],
						[(p.ordinal, p.char_start, p.char_end) for p in pieces],
					)
				)
			flat = [t for _, ts, _ in texts_by_capture for t in ts]
			vectors = iter((await embedder.embed_documents(flat)).vectors)
			for capture_id, ts, spans in texts_by_capture:
				for chunk_text_, (ordinal, start, end) in zip(ts, spans, strict=True):
					vector = next(vectors)
					await conn.execute(
						text(
							"INSERT INTO chunks (id, user_id, capture_id, ordinal, "
							"text, char_start, char_end, embedding, "
							"embedding_model, embedding_dim) "
							"VALUES (:id, :user_id, :capture_id, :ordinal, :text, "
							":s, :e, CAST(:embedding AS vector), :model, :dim)"
						),
						{
							"id": uuid7(),
							"user_id": eval_user,
							"capture_id": capture_id,
							"ordinal": ordinal,
							"text": chunk_text_,
							"s": start,
							"e": end,
							"embedding": "["
							+ ",".join(f"{v:.8f}" for v in vector)
							+ "]",
							"model": embedder.model,
							"dim": embedder.dim,
						},
					)

		modes = {"vector": Recall(), "keyword": Recall(), "fused": Recall()}
		async with sessions() as session:
			await bind_user(session, eval_user)
			for q in questions:
				expected = {capture_ids[c] for c in q.expected}
				result = await search(
					session,
					user_id=eval_user,
					query=q.question,
					embedder=embedder,
					k=10,
				)
				chunk_to_capture = {h.chunk_id: h.capture_id for h in result.hits}
				# Branch lists are chunk ids; map through the hits we know about, and
				# fall back to a lookup for chunks that did not make the fused top-k.
				missing = [
					c
					for c in [*result.vector_ranked, *result.keyword_ranked]
					if c not in chunk_to_capture
				]
				if missing:
					rows = (
						await session.execute(
							text(
								"SELECT id, capture_id FROM chunks WHERE id = ANY(:ids)"
							),
							{"ids": missing},
						)
					).all()
					chunk_to_capture.update({r[0]: r[1] for r in rows})
				modes["vector"].add(
					[chunk_to_capture[c] for c in result.vector_ranked], expected
				)
				modes["keyword"].add(
					[chunk_to_capture[c] for c in result.keyword_ranked], expected
				)
				modes["fused"].add([h.capture_id for h in result.hits], expected)
		return modes
	finally:
		async with admin.begin() as conn:
			await conn.execute(
				text("DELETE FROM users WHERE id = :id"), {"id": eval_user}
			)
		await admin.dispose()
		await app_engine.dispose()


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--fake", action="store_true", help="bag-of-words embedder")
	args = parser.parse_args(argv)

	embedder: Embedder
	if args.fake:
		from tests.fakes import FakeEmbedder

		embedder = FakeEmbedder()
	else:
		settings = get_settings()
		if settings.voyage_api_key is None:
			print("VOYAGE_API_KEY is unset; use --fake for an offline run")
			return 2
		embedder = VoyageEmbedder(
			api_key=settings.voyage_api_key.get_secret_value(),
			http=httpx.AsyncClient(),
			model=settings.embedding_model,
			dim=settings.embedding_dim,
		)

	modes = asyncio.run(run(embedder))
	print(f"embedder: {embedder.model}")
	print(f"{'mode':<8} {'recall@5':>9} {'recall@10':>10}")
	for name, r in modes.items():
		print(f"{name:<8} {r.at5:>9.2f} {r.at10:>10.2f}")
	fused = modes["fused"].at10
	if fused < THRESHOLD_FUSED_RECALL_AT_10:
		bar = THRESHOLD_FUSED_RECALL_AT_10
		print(f"BELOW THRESHOLD: fused recall@10 {fused:.2f} < {bar}")
		return 1
	print(json.dumps({k: {"at5": v.at5, "at10": v.at10} for k, v in modes.items()}))
	return 0


if __name__ == "__main__":
	sys.exit(main())
