"""Hybrid retrieval fused with reciprocal rank fusion (ADR 0005).

Two retrievers with opposite blind spots: the vector branch finds paraphrase and mood,
the lexical branch finds names, dates and rare tokens. Their scores are not comparable
(a cosine distance vs a ts_rank), so they are fused on *rank*: score = sum 1/(60+rank).
Nothing corpus-specific to tune. Both branches carry the tenant predicate (ADR 0010).
"""

import datetime as dt
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.services.embeddings import Embedder

RRF_K = 60


@dataclass(frozen=True)
class Hit:
	chunk_id: uuid.UUID
	capture_id: uuid.UUID | None
	derivation_id: uuid.UUID | None
	text: str
	created_at: dt.datetime
	vector_rank: int | None
	keyword_rank: int | None
	score: float


@dataclass
class RetrievalResult:
	hits: list[Hit]
	vector_ranked: list[uuid.UUID] = field(default_factory=list)
	keyword_ranked: list[uuid.UUID] = field(default_factory=list)


_COLUMNS = "id, capture_id, derivation_id, text, created_at"

_VECTOR_SQL = text(
	f"""
	SELECT {_COLUMNS}
	FROM chunks
	WHERE user_id = :user_id AND superseded_at IS NULL
		AND (:include_faith OR space = 'main')
	ORDER BY embedding <=> CAST(:query_vector AS vector)
	LIMIT :limit
	"""
)

_KEYWORD_SQL = text(
	f"""
	SELECT {_COLUMNS}
	FROM chunks, websearch_to_tsquery('english', :query) AS q
	WHERE user_id = :user_id AND superseded_at IS NULL AND tsv @@ q
		AND (:include_faith OR space = 'main')
	ORDER BY ts_rank_cd(tsv, q) DESC, created_at DESC
	LIMIT :limit
	"""
)


def _to_pgvector(vector: list[float]) -> str:
	return "[" + ",".join(f"{v:.8f}" for v in vector) + "]"


_TOKEN = re.compile(r"[A-Za-z0-9']+")


def lexical_query(question: str) -> str:
	"""OR the terms. websearch_to_tsquery ANDs them by default, so a question such
	as "what did I need to talk to Tunde about" never matches a chunk whole; with OR,
	ts_rank_cd still ranks the chunk matching the most terms first. Measured in
	evals/retrieval (offline embedder): keyword recall@10 0.38 -> 1.00 from this."""
	terms = [t for t in _TOKEN.findall(question) if len(t) > 1]
	return " OR ".join(terms) if terms else question


def rrf(*ranked_lists: list[uuid.UUID], k: int = RRF_K) -> dict[uuid.UUID, float]:
	scores: dict[uuid.UUID, float] = {}
	for ranked in ranked_lists:
		for rank, chunk_id in enumerate(ranked, start=1):
			scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
	return scores


async def search(
	session: AsyncSession,
	*,
	user_id: uuid.UUID,
	query: str,
	embedder: Embedder,
	k: int = 8,
	candidates: int = 40,
	ef_search: int = 100,
	include_faith: bool = True,
) -> RetrievalResult:
	"""Top-k chunks for `query`, one per capture, fused across both branches.

	`user_id` is bound by the caller from the authenticated principal; it is passed
	here (not read from any request) and applied in both branches on top of RLS.
	`include_faith=False` leaves faith notes out (ADR 0042), in both branches, on the
	chunks table alone.
	"""
	query_vector = await embedder.embed_query(query)

	# Post-filtered HNSW can return fewer than `candidates` rows when the tenant
	# filter is selective; a larger ef_search widens the graph walk for this
	# transaction only (pgvector's own mitigation, ADR 0010).
	await session.execute(text(f"SET LOCAL hnsw.ef_search = {int(ef_search)}"))
	vector_rows = (
		await session.execute(
			_VECTOR_SQL,
			{
				"user_id": user_id,
				"query_vector": _to_pgvector(query_vector),
				"limit": candidates,
				"include_faith": include_faith,
			},
		)
	).all()
	keyword_rows = (
		await session.execute(
			_KEYWORD_SQL,
			{
				"user_id": user_id,
				"query": lexical_query(query),
				"limit": candidates,
				"include_faith": include_faith,
			},
		)
	).all()

	by_id: dict[
		uuid.UUID, tuple[uuid.UUID | None, uuid.UUID | None, str, dt.datetime]
	] = {}
	for row in [*vector_rows, *keyword_rows]:
		by_id[row[0]] = (row[1], row[2], row[3], row[4])
	vector_ranked = [row[0] for row in vector_rows]
	keyword_ranked = [row[0] for row in keyword_rows]
	vector_rank = {cid: i for i, cid in enumerate(vector_ranked, start=1)}
	keyword_rank = {cid: i for i, cid in enumerate(keyword_ranked, start=1)}

	fused = sorted(
		rrf(vector_ranked, keyword_ranked).items(), key=lambda kv: (-kv[1], str(kv[0]))
	)
	hits: list[Hit] = []
	seen_sources: set[uuid.UUID] = set()
	for chunk_id, score in fused:
		capture_id, derivation_id, chunk_text, created_at = by_id[chunk_id]
		# One chunk per capture: overlap and derivation rows would otherwise let a
		# single capture take several of the k slots. A derivation that outlived its
		# note (ADR 0022) is its own source.
		source = capture_id or derivation_id
		assert source is not None, "ck_chunks_has_owner"
		if source in seen_sources:
			continue
		seen_sources.add(source)
		hits.append(
			Hit(
				chunk_id=chunk_id,
				capture_id=capture_id,
				derivation_id=derivation_id,
				text=chunk_text,
				created_at=created_at,
				vector_rank=vector_rank.get(chunk_id),
				keyword_rank=keyword_rank.get(chunk_id),
				score=score,
			)
		)
		if len(hits) == k:
			break
	return RetrievalResult(
		hits=hits, vector_ranked=vector_ranked, keyword_ranked=keyword_ranked
	)
