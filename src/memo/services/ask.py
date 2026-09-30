"""Ask: question -> grounded, streamed answer with citations chosen before generation.

Plain async code over a session, an embedder and two providers. `run_turn` is an async
generator of SSE-shaped events; the router only serialises them. The `retrieval` event
fires before the first token on purpose: citations are a retrieval artifact, chosen by
the system, so the model is never asked to produce one (design doc, Step 3).
"""

import asyncio
import datetime as dt
import re
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
from memo.db import bind_user, commit_and_rebind
from memo.ids import uuid7
from memo.models import (
	Chunk,
	Derivation,
	LLMCallRow,
	Message,
	MessageCitation,
	Person,
	User,
)
from memo.prompts import answer_v2, rewrite_v1
from memo.schemas.derivations import MENTION, MOOD_KINDS
from memo.schemas.people import IN_TOUCH
from memo.services import timewindow
from memo.services.embeddings import Embedder
from memo.services.extraction import RawCompletion
from memo.services.faith import faith_hidden
from memo.services.llm import LLMCall
from memo.services.morning import tz_or_utc
from memo.services.people import load_people, named_in
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


# --- what Ask can know exactly (ADR 0045) --------------------------------------------

# Items dated in the question's window, listed ahead of what retrieval finds. A cap,
# because "this month" can hold more than an answer should read out.
DATED_LIMIT = 15


@dataclass(frozen=True)
class Named:
	"""A person the question names, with their last recorded contact."""

	person: Person
	last: Derivation | None
	mentions: int


def _item_days(d: Derivation, zone: ZoneInfo) -> list[dt.date]:
	"""The days an item belongs to: an event's day, a todo's due date, and the day a
	todo was ticked off ("what did I get done last week?"). A payload that doesn't
	parse is skipped, not fatal: this is a listing, not validation."""
	days: list[dt.date] = []
	p = d.payload
	try:
		if d.kind == "event" and p.get("starts_at"):
			days.append(
				dt.datetime.fromisoformat(p["starts_at"]).astimezone(zone).date()
			)
		if d.kind == "todo" and p.get("due"):
			days.append(dt.date.fromisoformat(p["due"]))
	except (TypeError, ValueError):
		pass
	if d.kind == "todo" and d.completed_at is not None:
		days.append(d.completed_at.astimezone(zone).date())
	return days


async def dated_items(
	session: AsyncSession,
	user_id: uuid.UUID,
	window: timewindow.Window,
	zone: ZoneInfo,
) -> list[Derivation]:
	"""Todos and events that fall in `window`, earliest first. Read from the table the
	Planner reads, not searched: "what's on this week" has no words to match."""
	rows = await session.scalars(
		select(Derivation).where(
			Derivation.user_id == user_id,
			Derivation.kind.in_(("todo", "event")),
			Derivation.status != "rejected",
		)
	)
	found: list[tuple[dt.date, str, Derivation]] = []
	for d in rows.all():
		days = [day for day in _item_days(d, zone) if window.contains(day)]
		if days:
			found.append((min(days), str(d.payload.get("starts_at") or ""), d))
	found.sort(key=lambda t: (t[0], t[1], str(t[2].id)))
	return [d for _, _, d in found[:DATED_LIMIT]]


async def named_people(
	session: AsyncSession, user_id: uuid.UUID, question: str
) -> list[Named]:
	named = named_in(question, await load_people(session, user_id))
	if not named:
		return []
	mentions = (
		await session.scalars(
			select(Derivation).where(
				Derivation.user_id == user_id,
				Derivation.kind == MENTION,
				Derivation.status != "rejected",
				Derivation.person_id.in_([p.id for p in named]),
			)
		)
	).all()
	out = []
	for person in named:
		theirs = [m for m in mentions if m.person_id == person.id]
		in_touch = [
			m
			for m in theirs
			if m.payload.get("contact") in IN_TOUCH and m.payload.get("occurred_on")
		]
		last = max(
			in_touch,
			key=lambda m: (str(m.payload["occurred_on"]), m.created_at),
			default=None,
		)
		out.append(Named(person, last, len(theirs)))
	return out


async def pinned_hits(
	session: AsyncSession,
	user_id: uuid.UUID,
	derivations: list[Derivation],
	*,
	include_faith: bool,
) -> dict[uuid.UUID, Hit]:
	"""The chunk that stands for each derivation in the notes list, by derivation id:
	its note's opening chunk, so the model reads the note and everything taken from
	it; or its own chunk when it has no note (deleted, or added by hand). A faith note
	stays out of Home's Ask (ADR 0042): its item is shown by its own chunk instead."""
	capture_ids = {d.capture_id for d in derivations if d.capture_id is not None}
	openings: dict[uuid.UUID, Chunk] = {}
	if capture_ids:
		rows = await session.scalars(
			select(Chunk)
			.where(
				Chunk.user_id == user_id,
				Chunk.capture_id.in_(capture_ids),
				Chunk.derivation_id.is_(None),
				Chunk.superseded_at.is_(None),
				*(() if include_faith else (Chunk.space == "main",)),
			)
			.order_by(Chunk.ordinal.desc())
		)
		# Later ordinals first, so the opening chunk is the one each note keeps.
		openings = {c.capture_id: c for c in rows.all() if c.capture_id is not None}
	own_ids = [d.id for d in derivations if d.capture_id not in openings]
	own: dict[uuid.UUID, Chunk] = {}
	if own_ids:
		rows = await session.scalars(
			select(Chunk).where(
				Chunk.user_id == user_id,
				Chunk.derivation_id.in_(own_ids),
				Chunk.superseded_at.is_(None),
			)
		)
		own = {c.derivation_id: c for c in rows.all() if c.derivation_id is not None}
	out: dict[uuid.UUID, Hit] = {}
	for d in derivations:
		chunk = (
			openings.get(d.capture_id) if d.capture_id is not None else None
		) or own.get(d.id)
		if chunk is None:
			continue  # not embedded yet; retrieval may still find its note
		out[d.id] = Hit(
			chunk_id=chunk.id,
			capture_id=chunk.capture_id,
			derivation_id=chunk.derivation_id,
			text=chunk.text,
			created_at=chunk.created_at,
			vector_rank=None,
			keyword_rank=None,
			score=0.0,
		)
	return out


def _source(hit: Hit) -> uuid.UUID:
	source = hit.capture_id or hit.derivation_id
	assert source is not None, "ck_chunks_has_owner"
	return source


async def gather_hits(
	ctx: AskContext,
	user_id: uuid.UUID,
	question: str,
	query: str,
	*,
	zone: ZoneInfo,
	today: dt.date,
	include_faith: bool,
) -> tuple[list[Hit], timewindow.Window | None, list[Named], dict[uuid.UUID, Hit]]:
	"""Pinned first - the last contact with anyone named, then items dated in the
	question's days - then retrieval inside those days, then ordinary retrieval to
	fill up to top_k. One source appears once."""
	session = ctx.session
	window = timewindow.parse(query, today)
	if window is None and query != question:
		window = timewindow.parse(question, today)
	if window is None and (word := timewindow.temporal_word(question)):
		log.info("ask.time_unparsed", word=word)
	# The rewrite resolves "her" to a name on a follow-up; the question has the rest.
	named = await named_people(
		session, user_id, question if query == question else f"{question} {query}"
	)
	dated = await dated_items(session, user_id, window, zone) if window else []
	pins = await pinned_hits(
		session,
		user_id,
		[n.last for n in named if n.last is not None] + dated,
		include_faith=include_faith,
	)

	hits: list[Hit] = []
	seen: set[uuid.UUID] = set()

	def add(hit: Hit) -> None:
		if _source(hit) not in seen:
			seen.add(_source(hit))
			hits.append(hit)

	for hit in pins.values():
		add(hit)
	pinned = len(hits)
	query_vector = await ctx.embedder.embed_query(query)
	passes = [window.bounds(zone), None] if window else [None]
	for bounds in passes:
		result = await search(
			session,
			user_id=user_id,
			query=query,
			embedder=ctx.embedder,
			k=ctx.top_k,
			ef_search=ctx.ef_search,
			include_faith=include_faith,
			window=bounds,
			query_vector=query_vector,
		)
		for hit in result.hits:
			if len(hits) - pinned >= ctx.top_k:
				break
			add(hit)
	if window:
		log.info(
			"ask.window",
			days=(window.end - window.start).days + 1,
			dated=len(dated),
			pinned=pinned,
		)
	return hits, window, named, pins


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
	# Who wrote an item that has no note: the extractor (so its note was deleted), or
	# the person, by hand (a Planner item, a "Spoke to them") - never from a note.
	by_hand: set[uuid.UUID] = set()
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
				# A read of how a note sounded is memo's guess, not something the
				# note says (ADR 0058): Ask answers from the notes.
				Derivation.kind.not_in(MOOD_KINDS),
			)
			.order_by(Derivation.created_at, Derivation.id)
		)
		derivations = list(rows)
		# A linked mention reads as the person memo knows ("Mum" in the note,
		# "Folake Shonibare" in the line), so both names can match the question.
		person_ids = {d.person_id for d in derivations if d.person_id is not None}
		names: dict[uuid.UUID, str] = {}
		if person_ids:
			# Not dict(result): a Result has keys(), so dict() treats it as a mapping
			# and subscripts it - the crash that cut Ask's stream on 2026-09-29.
			rows_p = await session.execute(
				select(Person.id, Person.name).where(
					Person.user_id == user_id, Person.id.in_(person_ids)
				)
			)
			names = {pid: pname for pid, pname in rows_p.all()}
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
			if d.status == "user_created":
				by_hand.add(d.id)
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
					"(added by hand, not from a note)"
					if h.derivation_id in by_hand
					else "(the note this came from was deleted)",
					[by_id[h.derivation_id]],
				)
			)
		else:
			notes.append(answer_v2.Note(i, h.created_at, h.text))
	return notes, dt.datetime.now(zone).date(), zone


INTERNAL_ERROR = "Something went wrong on memo's side. It has been logged."
RATE_LIMITED = "An AI provider is rate-limiting memo right now. Try again in a minute."


def _for_the_user(exc: RetryableError | TerminalError) -> str:
	"""What the app shows. A provider's own 429 text - quotas, billing pages - is for
	the logs, not for the person asking; other reasons are kept, they say what to do."""
	# Two spellings: "HTTP 429" from an HTTP provider (classify_status, e.g. the
	# embedder), "anthropic 429" from the Claude SDK (_map_error).
	if isinstance(exc, RetryableError) and re.match(
		r"(HTTP|anthropic) 429\b", str(exc)
	):
		return RATE_LIMITED
	return str(exc)


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

		user = await session.get(User, user_id)
		zone = tz_or_utc(user.timezone if user is not None else "UTC")
		hits, window, named, pins = await gather_hits(
			ctx,
			user_id,
			question,
			query,
			zone=zone,
			today=dt.datetime.now(zone).date(),
			# Home's Ask leaves faith notes out while Faith is on (ADR 0042).
			include_faith=not faith_hidden(user),
		)
		for rank, hit in enumerate(hits, start=1):
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
					citation_payload(h, i) for i, h in enumerate(hits, start=1)
				]
			},
		)

		notes, today, zone = await _notes(session, user_id, hits)
		name = user.display_name if user is not None else None
		number = {_source(h): i for i, h in enumerate(hits, start=1)}
		people = [
			answer_v2.person_fact(
				n.person.name,
				list(n.person.aliases or []),
				dt.date.fromisoformat(n.last.payload["occurred_on"])
				if n.last is not None
				else None,
				n.last.payload.get("contact") if n.last is not None else None,
				number.get(_source(pins[n.last.id]))
				if n.last is not None and n.last.id in pins
				else None,
				n.mentions,
			)
			for n in named
		]
		messages: list[MessageParam] = [
			{"role": role, "content": txt}  # type: ignore[typeddict-item]
			for role, txt in history
		]
		messages.append(
			{
				"role": "user",
				"content": answer_v2.user_turn(
					question,
					notes,
					today=today,
					zone=zone,
					name=name,
					about=window.describe() if window else None,
					people=people,
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
			citations=len(hits),
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
		assistant.error = _for_the_user(exc)
		await commit_and_rebind(session, user_id)
		log.warning("ask.failed", message_id=str(assistant.id), error=str(exc))
		yield Event(
			"error",
			{
				"code": "provider_error",
				"message": assistant.error,
				"retryable": isinstance(exc, RetryableError),
			},
		)
	except Exception:
		# A bug, not a provider. Without this the stream just stops: the app shows
		# "connection lost" for what is really a crash, and the message stays
		# `streaming` forever. Say it failed, keep what was written, log the trace.
		log.exception("ask.crashed", message_id=str(assistant.id))
		await session.rollback()
		await bind_user(session, user_id)
		assistant.text = "".join(partial) or None
		assistant.status = "failed"
		assistant.error = INTERNAL_ERROR
		await commit_and_rebind(session, user_id)
		yield Event(
			"error",
			{"code": "internal_error", "message": INTERNAL_ERROR, "retryable": False},
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
