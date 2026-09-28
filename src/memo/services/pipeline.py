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
from memo.models import Capture, Derivation, JobRun, LLMCallRow, Recording, User
from memo.services.extraction import ExtractionFailed, ExtractorLike
from memo.services.llm import LLMCall
from memo.services.retry import RetryableError, TerminalError, backoff_delay
from memo.services.storage import ObjectStore
from memo.services.transcription import Transcriber
from memo.status import CaptureStatus, transition

log = structlog.get_logger("memo.pipeline")

# A `running` row older than this is a crashed worker's, and may be reclaimed.
STALE_RUN = dt.timedelta(minutes=15)


@dataclass
class PipelineContext:
	session: AsyncSession
	transcriber: Transcriber
	object_store: ObjectStore
	extractor: ExtractorLike
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


async def _not_yet(ctx: PipelineContext, capture: Capture) -> None:
	# Embedding (Stage 4) slots in here; until then the capture passes through so
	# the status machine stays the final shape.
	pass


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
		version=lambda ctx: "noop",
		run=_not_yet,
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
		# Status advances even for a stage that is skipped as already done, so a re-run
		# that resumes mid-pipeline still walks the machine to `ready` in order.
		if capture.status != stage.status.value:
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
