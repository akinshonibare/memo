"""A derivation's own chunk: what it looks like to retrieval, and keeping it current.

The pipeline writes one chunk per derivation when a capture is processed (dual
granularity, ADR 0017). Before this module, nothing touched that chunk again: an item
the user added never got one, and an edit left the old text answering questions
(ADR 0021's open gap). Now the two write paths - add and edit - enqueue a re-embed,
and rejection supersedes the chunk directly.
"""

import datetime as dt
import hashlib
import uuid
from collections.abc import Sequence

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from memo.ids import uuid7
from memo.models import Chunk, Derivation
from memo.schemas.derivations import FAITH_KINDS, MOOD_KINDS, NOT_ITEMS
from memo.services.embeddings import Embedder
from memo.services.queue import Queue

log = structlog.get_logger("memo.derivation_index")


_CONTACT_WORDS = {
	"in_person": "met in person",
	"call": "spoke on the phone",
	"message": "messaged",
	"unspecified": "was in touch",
}


def _mention_text(payload: dict[str, object]) -> str:
	"""A person in a note (ADR 0029), worded so "when did I last speak to Tunde?"
	matches the contact and its resolved date, not only the note's own words."""
	name = str(payload.get("full_name") or payload.get("name") or "")
	parts = [f"person: {name}"]
	if payload.get("hint"):
		parts.append(str(payload["hint"]))
	contact = str(payload.get("contact") or "none")
	if contact in _CONTACT_WORDS and payload.get("occurred_on"):
		parts.append(f"{_CONTACT_WORDS[contact]} on {payload['occurred_on']}")
	if payload.get("context"):
		parts.append(str(payload["context"]))
	return ". ".join(parts)


def derivation_text(derivation: Derivation) -> str:
	payload = derivation.payload
	if derivation.kind == "mention":
		return _mention_text(payload)
	parts = [f"{derivation.kind}: {payload.get('title', '')}"]
	# The resolved date is what a question like "what's on 6 October" matches on.
	if payload.get("due"):
		parts.append(f"due {payload['due']}")
	if payload.get("starts_at"):
		parts.append(f"starts {payload['starts_at']}")
	for key in ("summary", "notes", "location"):
		if payload.get(key):
			parts.append(str(payload[key]))
	return ". ".join(parts)


def text_fingerprint(text: str) -> str:
	"""Part of the job id, so an edit made while an earlier re-embed is running gets
	its own job instead of being dropped as a duplicate of the stale one."""
	return hashlib.sha256(text.encode()).hexdigest()[:16]


async def enqueue_reembed(queue: Queue, derivation: Derivation) -> None:
	"""Called after the add or edit is committed. A failed enqueue loses nothing the
	user can see - the derivation is saved, and Ask reads it live through its note -
	so it is logged rather than surfaced."""
	request_id = structlog.contextvars.get_contextvars().get("request_id")
	try:
		await queue.enqueue_embed_derivation(
			derivation.id,
			derivation.user_id,
			text_fingerprint(derivation_text(derivation)),
			request_id,
		)
	except Exception:
		log.exception("enqueue.failed", derivation_id=str(derivation.id))


def ordinal_of(live: Sequence[Chunk]) -> int:
	return live[0].ordinal if live else 0


async def _replace_chunk(
	session: AsyncSession,
	embedder: Embedder,
	derivation: Derivation,
	body: str,
	vector: list[float],
	ordinal: int,
) -> None:
	# Superseded, not deleted: a past answer's citation still resolves to what it saw.
	await session.execute(
		update(Chunk)
		.where(
			Chunk.derivation_id == derivation.id,
			Chunk.user_id == derivation.user_id,
			Chunk.superseded_at.is_(None),
		)
		.values(superseded_at=dt.datetime.now(dt.UTC))
	)
	session.add(
		Chunk(
			id=uuid7(),
			user_id=derivation.user_id,
			capture_id=derivation.capture_id,
			derivation_id=derivation.id,
			ordinal=ordinal,
			text=body,
			char_start=0,
			char_end=0,
			embedding=vector,
			embedding_model=embedder.model,
			embedding_dim=embedder.dim,
		)
	)


async def reembed_derivation(
	session: AsyncSession,
	embedder: Embedder,
	derivation_id: uuid.UUID,
	user_id: uuid.UUID,
) -> str:
	"""Make the derivation's live chunk match its current text. Idempotent: a job
	that runs twice, or late, finds the chunk already current and does nothing.

	Returns what happened, for the job log: gone | rejected | faith | activity | mood
	| unchanged | embedded. The caller commits."""
	derivation = await session.scalar(
		select(Derivation).where(
			Derivation.id == derivation_id, Derivation.user_id == user_id
		)
	)
	if derivation is None:
		return "gone"
	if derivation.status == "rejected":
		return "rejected"  # the reject path already superseded its chunk
	if derivation.kind in NOT_ITEMS and derivation.kind not in ("mention",):
		# Prayers, passages (ADR 0042), activities (ADR 0043) and moods (ADR 0058) are
		# never embedded.
		if derivation.kind in FAITH_KINDS:
			return "faith"
		return "mood" if derivation.kind in MOOD_KINDS else "activity"

	body = derivation_text(derivation)
	live = (
		await session.scalars(
			select(Chunk).where(
				Chunk.derivation_id == derivation.id,
				Chunk.user_id == user_id,
				Chunk.superseded_at.is_(None),
			)
		)
	).all()
	if len(live) == 1 and live[0].text == body:
		return "unchanged"

	result = await embedder.embed_documents([body])
	await _replace_chunk(
		session, embedder, derivation, body, result.vectors[0], ordinal_of(live)
	)
	log.info(
		"derivation.reembedded",
		derivation_id=str(derivation.id),
		model=embedder.model,
		tokens=result.total_tokens,
	)
	return "embedded"


# One embedding call's worth: under Voyage's 3 requests a minute (the Atlas cap until a
# payment method is added), a sweep is one request however stale the index is.
STALE_BATCH = 64


async def stale_derivations(
	session: AsyncSession, limit: int = STALE_BATCH
) -> list[tuple[Derivation, Chunk]]:
	"""Items whose live chunk no longer reads as the item does now: chunks written
	before `derivation_text` learned dates (ADR 0021's open backfill), and any written
	before a later change to it. Compared in Python, because the text is built there;
	at one person's scale the scan is a few thousand short rows."""
	rows = await session.execute(
		select(Derivation, Chunk)
		.join(
			Chunk,
			(Chunk.derivation_id == Derivation.id)
			& (Chunk.user_id == Derivation.user_id),
		)
		.where(
			Chunk.superseded_at.is_(None),
			Derivation.status != "rejected",
		)
		.order_by(Derivation.created_at.desc(), Derivation.id)
	)
	stale = [(d, c) for d, c in rows.all() if c.text != derivation_text(d)]
	return stale[:limit]


async def refresh_stale_chunks(
	session: AsyncSession, embedder: Embedder, limit: int = STALE_BATCH
) -> int:
	"""Re-embed a batch of stale item chunks in one call. The caller commits."""
	stale = await stale_derivations(session, limit)
	if not stale:
		return 0
	bodies = [derivation_text(d) for d, _ in stale]
	result = await embedder.embed_documents(bodies)
	for (derivation, chunk), body, vector in zip(
		stale, bodies, result.vectors, strict=True
	):
		await _replace_chunk(session, embedder, derivation, body, vector, chunk.ordinal)
	log.info(
		"derivation.chunks_refreshed", count=len(stale), tokens=result.total_tokens
	)
	return len(stale)
