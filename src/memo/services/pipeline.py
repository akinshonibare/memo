"""Capture processing: the stages, the idempotency ledger, and failure handling.

Plain async functions that know nothing about arq (ADR 0004). `process_capture` is
callable from a test with a session and two fakes.

Each stage is a commit point. A run that dies mid-way resumes at the first stage whose
`job_runs` row is not `succeeded`, so a retry never re-bills a stage that already
finished (design doc, Step 4).
"""

import datetime as dt
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from memo.db import bind_user, commit_and_rebind
from memo.ids import uuid7
from memo.models import (
	Capture,
	Chunk,
	Derivation,
	JobRun,
	LLMCallRow,
	Recording,
	User,
)
from memo.services.chunking import CHUNKER_VERSION, chunk_text
from memo.services.embeddings import Embedder
from memo.services.extraction import ExtractionFailed, ExtractorLike
from memo.services.llm import LLMCall
from memo.services.retry import RetryableError, TerminalError, backoff_delay
from memo.services.storage import ObjectStore
from memo.services.transcription import Transcriber
from memo.status import CaptureStatus, transition

log = structlog.get_logger("memo.pipeline")

# A `running` row older than this is a crashed worker's, and may be reclaimed.
STALE_RUN = dt.timedelta(minutes=15)

_ORDER = [
	CaptureStatus.PENDING,
	CaptureStatus.TRANSCRIBING,
	CaptureStatus.EXTRACTING,
	CaptureStatus.EMBEDDING,
	CaptureStatus.READY,
]


@dataclass
class PipelineContext:
	session: AsyncSession
	transcriber: Transcriber
	object_store: ObjectStore
	extractor: ExtractorLike
	embedder: Embedder
	max_attempts: int = 5


@dataclass(frozen=True)
class Outcome:
	status: str
	retry_after: float | None = None


StageFn = Callable[[PipelineContext, Capture], Awaitable[None]]


@dataclass(frozen=True)
class Stage:
	name: str
	status: CaptureStatus
	version: Callable[[PipelineContext], str]
	run: StageFn
	applies: Callable[[Capture], bool]


async def _transcribe(ctx: PipelineContext, capture: Capture) -> None:
	recording = await ctx.session.scalar(
		select(Recording).where(
			Recording.id == capture.recording_id, Recording.user_id == capture.user_id
		)
	)
	if recording is None:
		raise TerminalError("audio capture has no recording row")
	if not await ctx.object_store.exists(recording.storage_key):
		raise TerminalError(f"recording bytes missing at {recording.storage_key}")

	started = time.perf_counter()
	audio = await ctx.object_store.get(recording.storage_key)
	transcript = await ctx.transcriber.transcribe(audio, recording.mime)
	capture.text = transcript.text
	if recording.duration_ms is None:
		recording.duration_ms = transcript.duration_ms
	log.info(
		"stage.transcribe",
		capture_id=str(capture.id),
		model=transcript.model,
		audio_bytes=len(audio),
		audio_ms=transcript.duration_ms,
		stage_ms=round((time.perf_counter() - started) * 1000),
	)


async def _record_calls(
	ctx: PipelineContext, capture: Capture, calls: list[LLMCall]
) -> None:
	request_id = structlog.contextvars.get_contextvars().get("request_id")
	for call in calls:
		ctx.session.add(
			LLMCallRow(
				id=uuid7(),
				user_id=capture.user_id,
				capture_id=capture.id,
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


async def _extract(ctx: PipelineContext, capture: Capture) -> None:
	if not capture.text or not capture.text.strip():
		raise TerminalError("nothing to extract: the capture has no text")
	user = await ctx.session.get(User, capture.user_id)
	timezone = user.timezone if user is not None else "UTC"
	version = ctx.extractor.version
	try:
		result = await ctx.extractor.extract(
			capture.text, captured_at=capture.created_at, timezone=timezone
		)
	except ExtractionFailed as exc:
		# Paid-for calls are recorded even when the outcome is a failure.
		await _record_calls(ctx, capture, exc.calls)
		capture.extraction_raw = exc.raw
		raise TerminalError(str(exc)) from exc
	await _record_calls(ctx, capture, result.calls)

	# Idempotent by replacement: a re-run of this version drops only what nobody has
	# reviewed. Confirmed, rejected and user-created rows are the user's, not ours.
	await ctx.session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.extractor_version == version,
			Derivation.status == "pending_review",
		)
	)
	for kind, item in result.extraction.items():
		ctx.session.add(
			Derivation(
				id=uuid7(),
				user_id=capture.user_id,
				capture_id=capture.id,
				kind=kind,
				payload=item.model_dump(mode="json", exclude={"confidence"}),
				status="pending_review",
				extractor_version=version,
				confidence=item.confidence,
			)
		)
	capture.extraction_raw = None
	log.info(
		"stage.extract",
		capture_id=str(capture.id),
		version=version,
		derivations=len(result.extraction.items()),
		calls=len(result.calls),
		cost_usd=str(sum((c.cost_usd for c in result.calls), start=0)),
	)


def _derivation_text(derivation: Derivation) -> str:
	payload = derivation.payload
	parts = [f"{derivation.kind}: {payload.get('title', '')}"]
	for key in ("summary", "notes", "location"):
		if payload.get(key):
			parts.append(str(payload[key]))
	return ". ".join(parts)


async def _embed(ctx: PipelineContext, capture: Capture) -> None:
	"""Dual granularity: capture chunks for recall, derivation rows for precision."""
	source = capture.text or ""
	pieces = chunk_text(source)
	derivations = (
		await ctx.session.scalars(
			select(Derivation)
			.where(
				Derivation.capture_id == capture.id,
				Derivation.user_id == capture.user_id,
				Derivation.status != "rejected",
			)
			.order_by(Derivation.created_at, Derivation.id)
		)
	).all()
	texts = [p.text for p in pieces] + [_derivation_text(d) for d in derivations]
	if not texts:
		raise TerminalError("nothing to embed: the capture has no text")

	result = await ctx.embedder.embed_documents(texts)

	# Supersede rather than delete: message_citations point at chunk ids, and a
	# citation must keep resolving to the text the answer was grounded in.
	now = dt.datetime.now(dt.UTC)
	await ctx.session.execute(
		text(
			"UPDATE chunks SET superseded_at = :now "
			"WHERE capture_id = :capture_id AND user_id = :user_id "
			"AND superseded_at IS NULL"
		),
		{"now": now, "capture_id": capture.id, "user_id": capture.user_id},
	)
	vectors = iter(result.vectors)
	for piece in pieces:
		ctx.session.add(
			Chunk(
				id=uuid7(),
				user_id=capture.user_id,
				capture_id=capture.id,
				derivation_id=None,
				ordinal=piece.ordinal,
				text=piece.text,
				char_start=piece.char_start,
				char_end=piece.char_end,
				embedding=next(vectors),
				embedding_model=ctx.embedder.model,
				embedding_dim=ctx.embedder.dim,
			)
		)
	for offset, derivation in enumerate(derivations, start=len(pieces)):
		ctx.session.add(
			Chunk(
				id=uuid7(),
				user_id=capture.user_id,
				capture_id=capture.id,
				derivation_id=derivation.id,
				ordinal=offset,
				text=_derivation_text(derivation),
				char_start=0,
				char_end=0,
				embedding=next(vectors),
				embedding_model=ctx.embedder.model,
				embedding_dim=ctx.embedder.dim,
			)
		)
	log.info(
		"stage.embed",
		capture_id=str(capture.id),
		model=ctx.embedder.model,
		chunks=len(pieces),
		derivation_chunks=len(derivations),
		tokens=result.total_tokens,
	)


STAGES: tuple[Stage, ...] = (
	Stage(
		name="transcribe",
		status=CaptureStatus.TRANSCRIBING,
		version=lambda ctx: ctx.transcriber.model,
		run=_transcribe,
		applies=lambda c: c.kind == "audio",
	),
	Stage(
		name="extract",
		status=CaptureStatus.EXTRACTING,
		version=lambda ctx: ctx.extractor.version,
		run=_extract,
		applies=lambda c: True,
	),
	Stage(
		name="embed",
		status=CaptureStatus.EMBEDDING,
		version=lambda ctx: f"{CHUNKER_VERSION}/{ctx.embedder.model}",
		run=_embed,
		applies=lambda c: True,
	),
)


async def _claim_run(
	session: AsyncSession, capture: Capture, stage: Stage, version: str
) -> uuid.UUID | None:
	"""Insert the idempotency row, or reclaim a failed/stale one. None = already done.

	The unique constraint on (capture_id, stage, version) is the enforcement point:
	two workers racing for the same stage both hit the constraint and exactly one
	gets a row back. No check-then-insert.
	"""
	now = dt.datetime.now(dt.UTC)
	stmt = (
		insert(JobRun)
		.values(
			id=uuid7(),
			capture_id=capture.id,
			user_id=capture.user_id,
			stage=stage.name,
			version=version,
			status="running",
			attempt=1,
			started_at=now,
		)
		.on_conflict_do_update(
			constraint="uq_job_runs_capture_stage_version",
			set_={
				"status": "running",
				"attempt": JobRun.attempt + 1,
				"started_at": now,
				"finished_at": None,
				"error": None,
			},
			where=(JobRun.status == "failed")
			| ((JobRun.status == "running") & (JobRun.started_at < now - STALE_RUN)),
		)
		.returning(JobRun.id)
	)
	return await session.scalar(stmt)


async def _finish_run(
	session: AsyncSession, run_id: uuid.UUID, status: str, error: str | None = None
) -> None:
	await session.execute(
		text(
			"UPDATE job_runs SET status = :status, finished_at = now(), error = :error "
			"WHERE id = :id"
		),
		{"status": status, "error": error, "id": run_id},
	)


async def process_capture(
	ctx: PipelineContext, capture_id: uuid.UUID, user_id: uuid.UUID
) -> Outcome:
	session = ctx.session
	await bind_user(session, user_id)
	# populate_existing: never trust a Capture already in this session's identity map;
	# the retry endpoint may have reset the row since it was last loaded.
	capture = await session.scalar(
		select(Capture)
		.where(Capture.id == capture_id, Capture.user_id == user_id)
		.execution_options(populate_existing=True)
	)
	if capture is None:
		# Enqueued before its transaction committed, or rolled back. Cheap to recheck.
		return Outcome(status="missing", retry_after=1.0)
	if capture.status in (CaptureStatus.READY.value, CaptureStatus.FAILED.value):
		return Outcome(status=capture.status)

	for stage in STAGES:
		if not stage.applies(capture):
			continue
		version = stage.version(ctx)
		# Status advances (forward only) even for a stage skipped as already done, so
		# a re-run that resumes mid-pipeline still walks the machine to `ready`.
		if _ORDER.index(CaptureStatus(capture.status)) < _ORDER.index(stage.status):
			transition(capture, stage.status)
		run_id = await _claim_run(session, capture, stage, version)
		await commit_and_rebind(session, user_id)
		if run_id is None:
			log.info("stage.skip", capture_id=str(capture.id), stage=stage.name)
			continue

		try:
			await stage.run(ctx, capture)
		except RetryableError as exc:
			await _finish_run(session, run_id, "failed", repr(exc))
			capture.attempt_count += 1
			if capture.attempt_count >= ctx.max_attempts:
				reason = (
					f"{stage.name}: gave up after {capture.attempt_count} attempts: "
					f"{exc}"
				)
				transition(capture, CaptureStatus.FAILED, failure_reason=reason)
				await commit_and_rebind(session, user_id)
				log.warning("capture.failed", capture_id=str(capture.id), reason=reason)
				return Outcome(status=capture.status)
			await commit_and_rebind(session, user_id)
			delay = backoff_delay(capture.attempt_count)
			log.info(
				"stage.retry",
				capture_id=str(capture.id),
				stage=stage.name,
				attempt=capture.attempt_count,
				delay_s=round(delay, 1),
				error=repr(exc),
			)
			return Outcome(status=capture.status, retry_after=delay)
		except TerminalError as exc:
			await _finish_run(session, run_id, "failed", repr(exc))
			reason = f"{stage.name}: {exc}"
			transition(capture, CaptureStatus.FAILED, failure_reason=reason)
			await commit_and_rebind(session, user_id)
			log.warning("capture.failed", capture_id=str(capture.id), reason=reason)
			return Outcome(status=capture.status)

		await _finish_run(session, run_id, "succeeded")
		await commit_and_rebind(session, user_id)

	transition(capture, CaptureStatus.READY)
	await commit_and_rebind(session, user_id)
	log.info("capture.ready", capture_id=str(capture.id))
	return Outcome(status=capture.status)
