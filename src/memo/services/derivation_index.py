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

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from memo.ids import uuid7
from memo.models import Chunk, Derivation
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


async def reembed_derivation(
	session: AsyncSession,
	embedder: Embedder,
	derivation_id: uuid.UUID,
	user_id: uuid.UUID,
) -> str:
	"""Make the derivation's live chunk match its current text. Idempotent: a job
	that runs twice, or late, finds the chunk already current and does nothing.

	Returns what happened, for the job log: gone | rejected | unchanged | embedded.
	The caller commits."""
	derivation = await session.scalar(
		select(Derivation).where(
			Derivation.id == derivation_id, Derivation.user_id == user_id
		)
	)
	if derivation is None:
		return "gone"
	if derivation.status == "rejected":
		return "rejected"  # the reject path already superseded its chunk

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
	now = dt.datetime.now(dt.UTC)
	# Superseded, not deleted: a past answer's citation still resolves to what it saw.
	await session.execute(
		update(Chunk)
		.where(
			Chunk.derivation_id == derivation.id,
			Chunk.user_id == user_id,
			Chunk.superseded_at.is_(None),
		)
		.values(superseded_at=now)
	)
	session.add(
		Chunk(
			id=uuid7(),
			user_id=user_id,
			capture_id=derivation.capture_id,
			derivation_id=derivation.id,
			ordinal=live[0].ordinal if live else 0,
			text=body,
			char_start=0,
			char_end=0,
			embedding=result.vectors[0],
			embedding_model=embedder.model,
			embedding_dim=embedder.dim,
		)
	)
	log.info(
		"derivation.reembedded",
		derivation_id=str(derivation.id),
		model=embedder.model,
		tokens=result.total_tokens,
	)
	return "embedded"
