"""Ask: question -> grounded, streamed answer with citations chosen before generation.

Plain async code over a session, an embedder and two providers. `run_turn` is an async
generator of SSE-shaped events; the router only serialises them. The `retrieval` event
fires before the first token on purpose: citations are a retrieval artifact, chosen by
the system, so the model is never asked to produce one (design doc, Step 3).
"""

import asyncio
import datetime as dt
import time
import uuid
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import dataclass
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import anthropic
import structlog
from anthropic import AsyncAnthropic
from anthropic.types import MessageParam
from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.db import commit_and_rebind
from memo.ids import uuid7
from memo.models import (
	Derivation,
	LLMCallRow,
	Message,
	MessageCitation,
	Person,
	User,
)
from memo.prompts import answer_v2, rewrite_v1
from memo.services.embeddings import Embedder
from memo.services.extraction import RawCompletion
from memo.services.llm import LLMCall
from memo.services.morning import tz_or_utc
from memo.services.retrieval import Hit, search
from memo.services.retry import RetryableError, TerminalError

log = structlog.get_logger("memo.ask")


# --- providers -----------------------------------------------------------------------


class TextProvider(Protocol):
	"""One-shot completion (the rewrite). Same shape as the extraction provider."""

	model: str

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion: ...


class AnswerProvider(Protocol):
	model: str

	def stream(
		self, *, system: str, messages: list[MessageParam]
	) -> AsyncGenerator[str, None]: ...

	def usage(self) -> LLMCall: ...


def _map_error(exc: Exception) -> Exception:
	if isinstance(exc, anthropic.RateLimitError):
		return RetryableError(f"anthropic 429: {exc.message}")
	if isinstance(exc, anthropic.APIStatusError):
		if exc.status_code >= 500:
			return RetryableError(f"anthropic {exc.status_code}")
		return TerminalError(f"anthropic {exc.status_code}: {exc.message}")
	if isinstance(exc, anthropic.APIConnectionError):
		return RetryableError(f"anthropic unreachable: {exc!r}")
	return exc


class AnthropicTextProvider:
	def __init__(self, client: AsyncAnthropic, *, model: str) -> None:
		self._client = client
		self.model = model

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		started = time.perf_counter()
		try:
			response = await self._client.messages.create(
				model=self.model, max_tokens=300, system=system, messages=messages
			)
		except Exception as exc:
			raise _map_error(exc) from exc
		usage = response.usage
		return RawCompletion(
			text="".join(b.text for b in response.content if b.type == "text"),
			model=response.model,
			input_tokens=usage.input_tokens,
			output_tokens=usage.output_tokens,
			cache_read_tokens=usage.cache_read_input_tokens or 0,
			cache_write_tokens=usage.cache_creation_input_tokens or 0,
			latency_ms=round((time.perf_counter() - started) * 1000),
		)


class AnthropicAnswerProvider:
	def __init__(self, client: AsyncAnthropic, *, model: str) -> None:
		self._client = client
		self.model = model
		self._last: LLMCall | None = None

	async def stream(
		self, *, system: str, messages: list[MessageParam]
	) -> AsyncGenerator[str, None]:
		started = time.perf_counter()
		try:
			# Leaving the `async with` - including on cancellation - closes the HTTP
			# stream, which is what stops the bill when the client has gone.
			async with self._client.messages.stream(
				model=self.model,
				max_tokens=2048,
				system=system,
				messages=messages,
				thinking={"type": "adaptive"},
				output_config={"effort": "low"},
			) as stream:
				async for delta in stream.text_stream:
					yield delta
				final = await stream.get_final_message()
		except Exception as exc:
			raise _map_error(exc) from exc
		usage = final.usage
		self._last = LLMCall.priced(
			provider="anthropic",
			model=final.model,
			stage="answer",
			input_tokens=usage.input_tokens,
			output_tokens=usage.output_tokens,
			cache_read_tokens=usage.cache_read_input_tokens or 0,
			cache_write_tokens=usage.cache_creation_input_tokens or 0,
			latency_ms=round((time.perf_counter() - started) * 1000),
		)

	def usage(self) -> LLMCall:
		if self._last is None:
			raise RuntimeError("usage() before the stream completed")
		return self._last


class UnconfiguredProvider:
	model = "unconfigured"

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		raise TerminalError("ask is not configured (ANTHROPIC_API_KEY is unset)")

	async def stream(
		self, *, system: str, messages: list[MessageParam]
	) -> AsyncGenerator[str, None]:
		raise TerminalError("ask is not configured (ANTHROPIC_API_KEY is unset)")
		yield ""  # pragma: no cover - makes this an async generator

	def usage(self) -> LLMCall:
		raise RuntimeError("no usage: provider is unconfigured")


def make_providers(settings: Settings) -> tuple[AnswerProvider, TextProvider]:
	if settings.anthropic_api_key is None:
		unconfigured = UnconfiguredProvider()
		return unconfigured, unconfigured
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return (
		AnthropicAnswerProvider(client, model=settings.answer_model),
		AnthropicTextProvider(client, model=settings.rewrite_model),
	)


# --- the turn ------------------------------------------------------------------------


@dataclass
class AskContext:
	session: AsyncSession
	embedder: Embedder
	answer_provider: AnswerProvider
	rewrite_provider: TextProvider
	top_k: int = 8
	history_turns: int = 6
	ef_search: int = 100


@dataclass(frozen=True)
class Event:
	name: str
	data: dict[str, Any]


def citation_payload(hit: Hit, rank: int) -> dict[str, Any]:
	return {
		"chunk_id": str(hit.chunk_id),
		"capture_id": str(hit.capture_id) if hit.capture_id else None,
		"derivation_id": str(hit.derivation_id) if hit.derivation_id else None,
		"rank": rank,
		"snippet": hit.text[:280],
		"created_at": hit.created_at.isoformat(),
	}


async def _history(
	session: AsyncSession, assistant: Message, limit: int
) -> list[tuple[str, str]]:
	rows = (
		await session.execute(
			select(Message.role, Message.text)
			.where(
				Message.conversation_id == assistant.conversation_id,
				Message.user_id == assistant.user_id,
				Message.status == "done",
				Message.text.is_not(None),
				Message.created_at < assistant.created_at,
			)
			.order_by(Message.created_at.desc(), Message.id.desc())
			.limit(limit)
		)
	).all()
	return [(role, txt or "") for role, txt in reversed(rows)]


async def _record_call(
	session: AsyncSession, assistant: Message, call: LLMCall
) -> None:
	request_id = structlog.contextvars.get_contextvars().get("request_id")
	session.add(
		LLMCallRow(
			id=uuid7(),
			user_id=assistant.user_id,
			capture_id=None,
			message_id=assistant.id,
			stage=call.stage,
			provider=call.provider,
			model=call.model,
			input_tokens=call.input_tokens,
			output_tokens=call.output_tokens,
			cache_read_tokens=call.cache_read_tokens,
			cache_write_tokens=call.cache_write_tokens,
			latency_ms=call.latency_ms,
			cost_usd=call.cost_usd,
			request_id=str(request_id)[:64] if request_id else None,
		)
	)


async def rewrite_query(
	ctx: AskContext, assistant: Message, question: str, history: list[tuple[str, str]]
) -> str:
	"""Standalone query for retrieval; the question itself when no rewrite is needed."""
	# `history` holds (user, assistant) pairs from earlier turns only.
	if not rewrite_v1.needs_rewrite(question, prior_turns=len(history) // 2):
		return question
	started = time.perf_counter()
	raw = await ctx.rewrite_provider.complete(
		system=rewrite_v1.SYSTEM_PROMPT,
		messages=[{"role": "user", "content": rewrite_v1.user_turn(history, question)}],
	)
	rewritten = raw.text.strip().splitlines()[0].strip() if raw.text.strip() else ""
	assistant.rewrite_ms = round((time.perf_counter() - started) * 1000)
	await _record_call(
		ctx.session,
		assistant,
		LLMCall.priced(
			provider="anthropic",
			model=raw.model,
			stage="rewrite",
			input_tokens=raw.input_tokens,
			output_tokens=raw.output_tokens,
			cache_read_tokens=raw.cache_read_tokens,
			cache_write_tokens=raw.cache_write_tokens,
			latency_ms=raw.latency_ms,
		),
	)
	log.info("ask.rewrite", ms=assistant.rewrite_ms, changed=rewritten != question)
	if not rewritten or rewritten == question:
		return question
	assistant.rewritten_query = rewritten
	return rewritten


async def _notes(
	session: AsyncSession, user_id: uuid.UUID, hits: list[Hit]
) -> tuple[list[answer_v2.Note], dt.date, ZoneInfo]:
	"""The retrieved notes, each with what was extracted from it (ADR 0021).

	Derivations are read here, live, rather than trusted from their embedded chunks:
	a chunk is written once when the capture is processed, so it knows nothing of a
	later confirmation, edit, or addition. Rejected ones are left out."""
	user = await session.get(User, user_id)
	zone = tz_or_utc(user.timezone if user is not None else "UTC")
	capture_ids = {h.capture_id for h in hits if h.capture_id is not None}
	# A derivation whose note was deleted (ADR 0022) is retrieved on its own chunk,
	# so it is read by its own id rather than through a capture.
	orphan_ids = {
		h.derivation_id
		for h in hits
		if h.capture_id is None and h.derivation_id is not None
	}
	by_capture: dict[uuid.UUID, list[str]] = {}
	by_id: dict[uuid.UUID, str] = {}
	if capture_ids or orphan_ids:
		rows = await session.scalars(
			select(Derivation)
			.where(
				Derivation.user_id == user_id,
				or_(
					Derivation.capture_id.in_(capture_ids),
					Derivation.id.in_(orphan_ids),
				),
				Derivation.status != "rejected",
			)
			.order_by(Derivation.created_at, Derivation.id)
		)
		derivations = list(rows)
		# A linked mention reads as the person memo knows ("Mum" in the note,
		# "Folake Shonibare" in the line), so both names can match the question.
		person_ids = {d.person_id for d in derivations if d.person_id is not None}
		names: dict[uuid.UUID, str] = {}
		if person_ids:
			names = dict(
				(
					await session.execute(
						select(Person.id, Person.name).where(
							Person.user_id == user_id, Person.id.in_(person_ids)
						)
					)
				).tuples()
			)
		for d in derivations:
			line = answer_v2.extracted_line(
				d.kind,
				d.payload,
				d.status,
				zone,
				done=d.completed_at is not None,
				person=names.get(d.person_id) if d.person_id is not None else None,
			)
			by_id[d.id] = line
			if d.capture_id is not None:
				by_capture.setdefault(d.capture_id, []).append(line)
	notes = []
	for i, h in enumerate(hits, start=1):
		if h.capture_id is not None:
			notes.append(
				answer_v2.Note(
					i, h.created_at, h.text, by_capture.get(h.capture_id, [])
				)
			)
		elif h.derivation_id in by_id:
			notes.append(
				answer_v2.Note(
					i,
					h.created_at,
					"(the note this came from was deleted)",
					[by_id[h.derivation_id]],
				)
			)
		else:
			notes.append(answer_v2.Note(i, h.created_at, h.text))
	return notes, dt.datetime.now(zone).date(), zone


async def run_turn(ctx: AskContext, assistant: Message) -> AsyncGenerator[Event, None]:
	"""Drive one pending assistant message to done, yielding SSE events as it goes."""
	session = ctx.session
	user_id = assistant.user_id
	question_row = await session.scalar(
		select(Message)
		.where(
			Message.conversation_id == assistant.conversation_id,
			Message.user_id == user_id,
			Message.role == "user",
			Message.created_at <= assistant.created_at,
		)
		.order_by(Message.created_at.desc(), Message.id.desc())
	)
	question = (question_row.text if question_row else None) or ""
	assistant.status = "streaming"
	await commit_and_rebind(session, user_id)

	partial: list[str] = []
	started = time.perf_counter()
	try:
		history = await _history(session, assistant, ctx.history_turns)
		query = await rewrite_query(ctx, assistant, question, history)

		result = await search(
			session,
			user_id=user_id,
			query=query,
			embedder=ctx.embedder,
			k=ctx.top_k,
			ef_search=ctx.ef_search,
		)
		for rank, hit in enumerate(result.hits, start=1):
			session.add(
				MessageCitation(
					message_id=assistant.id,
					chunk_id=hit.chunk_id,
					user_id=user_id,
					rank=rank,
				)
			)
		await commit_and_rebind(session, user_id)
		yield Event(
			"retrieval",
			{
				"citations": [
					citation_payload(h, i) for i, h in enumerate(result.hits, start=1)
				]
			},
		)

		notes, today, zone = await _notes(session, user_id, result.hits)
		person = await session.get(User, user_id)
		name = person.display_name if person is not None else None
		messages: list[MessageParam] = [
			{"role": role, "content": txt}  # type: ignore[typeddict-item]
			for role, txt in history
		]
		messages.append(
			{
				"role": "user",
				"content": answer_v2.user_turn(
					question, notes, today=today, zone=zone, name=name
				),
			}
		)
		first_token_at: float | None = None
		async with aclosing(
			ctx.answer_provider.stream(
				system=answer_v2.SYSTEM_PROMPT, messages=messages
			)
		) as tokens:
			async for delta in tokens:
				if first_token_at is None:
					first_token_at = time.perf_counter()
					assistant.ttft_ms = round((first_token_at - started) * 1000)
				partial.append(delta)
				yield Event("token", {"delta": delta})

		call = ctx.answer_provider.usage()
		assistant.text = "".join(partial)
		assistant.status = "done"
		assistant.completed_at = dt.datetime.now(dt.UTC)
		await _record_call(session, assistant, call)
		await commit_and_rebind(session, user_id)
		log.info(
			"ask.done",
			message_id=str(assistant.id),
			ttft_ms=assistant.ttft_ms,
			total_ms=round((time.perf_counter() - started) * 1000),
			citations=len(result.hits),
			cost_usd=str(call.cost_usd),
		)
		yield Event(
			"done",
			{
				"message_id": str(assistant.id),
				"usage": {
					"input_tokens": call.input_tokens,
					"output_tokens": call.output_tokens,
					"cost_usd": str(call.cost_usd),
					"ms": round((time.perf_counter() - started) * 1000),
				},
			},
		)
	except (RetryableError, TerminalError) as exc:
		assistant.text = "".join(partial) or None
		assistant.status = "failed"
		assistant.error = str(exc)
		await commit_and_rebind(session, user_id)
		log.warning("ask.failed", message_id=str(assistant.id), error=str(exc))
		yield Event(
			"error",
			{
				"code": "provider_error",
				"message": str(exc),
				"retryable": isinstance(exc, RetryableError),
			},
		)
	except (asyncio.CancelledError, GeneratorExit):
		# The client went away mid-answer. The provider stream was closed by
		# `aclosing` above; persist what was generated so the row is not a blank.
		assistant.text = "".join(partial) or None
		assistant.status = "failed"
		assistant.error = f"client disconnected after {len(partial)} tokens"
		await commit_and_rebind(session, user_id)
		log.warning("ask.cancelled", message_id=str(assistant.id), tokens=len(partial))
		raise


async def replay(
	session: AsyncSession, assistant: Message
) -> AsyncGenerator[Event, None]:
	"""A finished (or failed) message, re-emitted as the same event sequence."""
	rows = (
		await session.execute(
			text(
				"SELECT c.chunk_id, k.capture_id, k.derivation_id, c.rank, k.text, "
				"k.created_at FROM message_citations c "
				"JOIN chunks k ON k.id = c.chunk_id "
				"WHERE c.message_id = :mid AND c.user_id = :uid ORDER BY c.rank"
			),
			{"mid": assistant.id, "uid": assistant.user_id},
		)
	).all()
	yield Event(
		"retrieval",
		{
			"citations": [
				{
					"chunk_id": str(r[0]),
					"capture_id": str(r[1]) if r[1] else None,
					"derivation_id": str(r[2]) if r[2] else None,
					"rank": r[3],
					"snippet": r[4][:280],
					"created_at": r[5].isoformat(),
				}
				for r in rows
			]
		},
	)
	if assistant.text:
		yield Event("token", {"delta": assistant.text})
	if assistant.status == "done":
		yield Event("done", {"message_id": str(assistant.id), "usage": None})
	else:
		yield Event(
			"error",
			{
				"code": "failed",
				"message": assistant.error or "failed",
				"retryable": False,
			},
		)


def new_turn(
	conversation_id: uuid.UUID, user_id: uuid.UUID, question: str
) -> tuple[Message, Message]:
	user_message = Message(
		id=uuid7(),
		conversation_id=conversation_id,
		user_id=user_id,
		role="user",
		text=question,
		status="done",
		completed_at=dt.datetime.now(dt.UTC),
	)
	assistant = Message(
		id=uuid7(),
		conversation_id=conversation_id,
		user_id=user_id,
		role="assistant",
		text=None,
		status="pending",
	)
	return user_message, assistant
