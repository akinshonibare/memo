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
from dataclasses import dataclass, field

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
from memo.prompts.describe_v1 import with_caption
from memo.schemas.derivations import ACTIVITY, FAITH_KINDS, UNREVIEWED
from memo.services.chunking import CHUNKER_VERSION, chunk_text
from memo.services.derivation_index import derivation_text, reembed_derivation
from memo.services.embeddings import Embedder
from memo.services.extraction import ExtractionFailed, ExtractorLike
from memo.services.faith import (
	SCRIPTURE_VERSION,
	FaithReaderLike,
	UnconfiguredFaithReader,
	apply_prayers,
	faith_on,
	might_pray,
	replace_passages,
)
from memo.services.habits import (
	HabitsReaderLike,
	UnconfiguredHabitsReader,
	apply_activities,
	habits_on,
	own_keys,
)
from memo.services.llm import LLMCall
from memo.services.people import PeopleReaderLike, apply_mentions, keyterms
from memo.services.reconcile import (
	ReconcilerLike,
	UnconfiguredReconciler,
	apply_changes,
)
from memo.services.reconcile import candidates as reconcile_candidates
from memo.services.retry import RetryableError, TerminalError, backoff_delay
from memo.services.storage import ObjectStore
from memo.services.transcription import Transcriber
from memo.services.vision import Describer, UnconfiguredDescriber
from memo.status import CaptureStatus, transition

log = structlog.get_logger("memo.pipeline")

# A `running` row older than this is a crashed worker's, and may be reclaimed.
STALE_RUN = dt.timedelta(minutes=15)

# Transcribing and describing are alternatives (audio vs photo), never both.
_ORDER = [
	CaptureStatus.PENDING,
	CaptureStatus.TRANSCRIBING,
	CaptureStatus.DESCRIBING,
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
	# Required, not defaulted: a pipeline built without a people reader must be a
	# TypeError, not notes silently marked as read for people (ADR 0029).
	people: PeopleReaderLike
	max_attempts: int = 5
	describer: Describer = field(default_factory=UnconfiguredDescriber)
	# Faith (ADR 0042). Defaulted, unlike people: it only ever runs for someone who has
	# turned Faith on, and an unconfigured reader fails the (optional) stage visibly.
	faith: FaithReaderLike = field(default_factory=UnconfiguredFaithReader)
	# Habits (ADR 0043), defaulted like faith: only ever read for someone with it on.
	habits: HabitsReaderLike = field(default_factory=UnconfiguredHabitsReader)
	# Reconcile (ADR 0048), defaulted: it runs only when a note has something to be
	# compared with, and an unconfigured reader fails the (optional) stage visibly.
	reconciler: ReconcilerLike = field(default_factory=UnconfiguredReconciler)


@dataclass(frozen=True)
class Outcome:
	status: str
	retry_after: float | None = None


StageFn = Callable[[PipelineContext, Capture], Awaitable[None]]


class StageOff(Exception):
	"""Raised by a stage its owner hasn't turned on (Habits, ADR 0043). The note is not
	read, and its ledger row is removed rather than marked succeeded: "succeeded" would
	mean read, and the backlog that runs once the switch is on would skip the note."""


@dataclass(frozen=True)
class Stage:
	name: str
	status: CaptureStatus
	version: Callable[[PipelineContext], str]
	run: StageFn
	applies: Callable[[Capture], bool]
	# An optional stage's failure never fails the note: it is recorded on the ledger
	# and the note goes on to `ready`. People, faith, habits and scripture are optional:
	# what else a note says is secondary to the note being saved and searchable.
	optional: bool = False


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
	# The names of people in earlier notes, so "Tunde" isn't heard as "Tuesday".
	terms = await keyterms(ctx.session, capture.user_id)
	transcript = await ctx.transcriber.transcribe(audio, recording.mime, keyterms=terms)
	# Written together, so the words always belong to the text shown (a retry that
	# transcribes again replaces both).
	capture.text = transcript.text
	capture.words = [list(w) for w in transcript.words]
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


async def _describe(ctx: PipelineContext, capture: Capture) -> None:
	"""A photo becomes text (ADR 0024), into the same field a transcript fills, so the
	stages after this one cannot tell a photo from a voice note."""
	photo = await ctx.session.scalar(
		select(Recording).where(
			Recording.id == capture.recording_id, Recording.user_id == capture.user_id
		)
	)
	if photo is None:
		raise TerminalError("image capture has no upload row")
	if not await ctx.object_store.exists(photo.storage_key):
		raise TerminalError(f"image bytes missing at {photo.storage_key}")
	started = time.perf_counter()
	image = await ctx.object_store.get(photo.storage_key)
	result = await ctx.describer.describe(image, photo.mime, caption=capture.caption)
	await _record_calls(ctx, capture, [result.call])
	# Your words first, then the description (ADR 0056), rebuilt from `caption` so a
	# re-run neither loses nor doubles them.
	capture.text = with_caption(capture.caption, result.text)
	log.info(
		"stage.describe",
		capture_id=str(capture.id),
		version=ctx.describer.version,
		image_bytes=len(image),
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


def _skip(stage: str, capture: Capture) -> None:
	"""A note with no words - a voice note where nothing was said - is not a failure.
	There is nothing to read, so no model is paid to read it, and the note goes on
	to `ready` with nothing extracted, like any other note that had nothing in it."""
	log.info(f"stage.{stage}.skipped", capture_id=str(capture.id), reason="no_text")


async def _extract(ctx: PipelineContext, capture: Capture) -> None:
	if not capture.text or not capture.text.strip():
		_skip("extract", capture)
		return
	user = await ctx.session.get(User, capture.user_id)
	timezone = user.timezone if user is not None else "UTC"
	# Auto-accept (ADR 0030): kept at once, but as 'accepted', never 'confirmed'.
	status = "accepted" if user is not None and user.auto_accept else "pending_review"
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
			Derivation.status.in_(UNREVIEWED),
		)
	)
	for kind, item in result.extraction.items():
		if kind == "idea" and capture.space == "faith":
			# A reflection is full of thoughts, and Home's Ideas shouldn't fill with
			# them (ADR 0042). Its todos and events still count: they're commitments.
			continue
		ctx.session.add(
			Derivation(
				id=uuid7(),
				user_id=capture.user_id,
				capture_id=capture.id,
				kind=kind,
				payload=item.model_dump(mode="json", exclude={"confidence"}),
				status=status,
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


async def _people(ctx: PipelineContext, capture: Capture) -> None:
	"""Who the note is about (ADR 0029). Its own model call, after extraction and
	before embedding, so each mention gets a chunk in the same pass as the rest."""
	if not capture.text or not capture.text.strip():
		_skip("people", capture)
		return
	user = await ctx.session.get(User, capture.user_id)
	timezone = user.timezone if user is not None else "UTC"
	author = user.display_name if user is not None else None
	version = ctx.people.version
	started = time.perf_counter()
	try:
		result = await ctx.people.read(
			capture.text,
			captured_at=capture.created_at,
			timezone=timezone,
			author=author,
		)
	except ExtractionFailed as exc:
		await _record_calls(ctx, capture, exc.calls)
		raise TerminalError(str(exc)) from exc
	await _record_calls(ctx, capture, result.calls)
	mentions = await apply_mentions(
		ctx.session,
		capture,
		result.extraction,
		version=version,
		timezone=timezone,
		auto_accept=user.auto_accept if user is not None else False,
	)
	log.info(
		"stage.people",
		capture_id=str(capture.id),
		version=version,
		mentions=len(mentions),
		linked=sum(1 for m in mentions if m.person_id is not None),
		stage_ms=round((time.perf_counter() - started) * 1000),
	)


async def _faith(ctx: PipelineContext, capture: Capture) -> None:
	"""The prayers in a note (ADR 0042), filed into Faith. Only for someone with Faith
	on, and only when the note has a prayer-ish word in it: most notes cost nothing."""
	user = await ctx.session.get(User, capture.user_id)
	if not faith_on(user) or not might_pray(capture.text):
		_skip("faith", capture)
		return
	assert user is not None
	result = await ctx.faith.read(
		capture.text or "", captured_at=capture.created_at, timezone=user.timezone
	)
	await _record_calls(ctx, capture, result.calls)
	made = await apply_prayers(
		ctx.session,
		capture,
		result.extraction,
		version=ctx.faith.version,
		extract_version=ctx.extractor.version,
	)
	log.info(
		"stage.faith",
		capture_id=str(capture.id),
		version=ctx.faith.version,
		prayers=len(made),
		linked=sum(1 for p in made if p.person_id is not None),
	)


async def _habits(ctx: PipelineContext, capture: Capture) -> None:
	"""What a note says you did (ADR 0043), counted towards habits. Only for someone
	with Habits on; every note of theirs, since an activity can be anything."""
	user = await ctx.session.get(User, capture.user_id)
	if not habits_on(user):
		raise StageOff("habits")
	if not (capture.text or "").strip():
		_skip("habits", capture)
		return
	assert user is not None
	result = await ctx.habits.read(
		capture.text or "",
		captured_at=capture.created_at,
		timezone=user.timezone,
		extra_keys=await own_keys(ctx.session, user.id),
	)
	await _record_calls(ctx, capture, result.calls)
	made = await apply_activities(
		ctx.session, capture, result.extraction, version=ctx.habits.version
	)
	log.info(
		"stage.habits",
		capture_id=str(capture.id),
		version=ctx.habits.version,
		activities=[a.payload.get("key") for a in made],
	)


async def _scripture(ctx: PipelineContext, capture: Capture) -> None:
	"""The Bible passages a note is about (ADR 0042): every faith note, and any note
	while Faith is on. No model call: references have a regular shape, and
	services/scripture parses and checks them."""
	user = await ctx.session.get(User, capture.user_id)
	found = await replace_passages(ctx.session, capture, enabled=faith_on(user))
	log.info("stage.scripture", capture_id=str(capture.id), passages=found)


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
				# Prayers, passages (ADR 0042) and activities (ADR 0043) answer
				# nothing in Ask.
				Derivation.kind.not_in((*FAITH_KINDS, ACTIVITY)),
			)
			.order_by(Derivation.created_at, Derivation.id)
		)
	).all()
	texts = [p.text for p in pieces] + [derivation_text(d) for d in derivations]
	if not texts:
		_skip("embed", capture)
		return

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
				space=capture.space,
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
				text=derivation_text(derivation),
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


async def _reconcile(ctx: PipelineContext, capture: Capture) -> None:
	"""Whether this note answers an open prayer or carries on an idea (ADR 0048). After
	embed, so the note's own embeddings find the ideas near it; only a note with
	something to compare with costs a model call."""
	if not (capture.text or "").strip():
		_skip("reconcile", capture)
		return
	user = await ctx.session.get(User, capture.user_id)
	listed = await reconcile_candidates(ctx.session, capture, user)
	if not listed:
		log.info("stage.reconcile", capture_id=str(capture.id), candidates=0)
		return
	result = await ctx.reconciler.read(
		capture.text or "",
		[c.listed for c in listed],
		captured_at=capture.created_at,
		timezone=user.timezone if user is not None else "UTC",
	)
	await _record_calls(ctx, capture, result.calls)
	made = await apply_changes(
		ctx.session, capture, result.result, listed, version=ctx.reconciler.version
	)
	log.info(
		"stage.reconcile",
		capture_id=str(capture.id),
		version=ctx.reconciler.version,
		candidates=len(listed),
		suggested=[u.payload.get("relation") for u in made],
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
		name="describe",
		status=CaptureStatus.DESCRIBING,
		version=lambda ctx: ctx.describer.version,
		run=_describe,
		applies=lambda c: c.kind == "image",
	),
	Stage(
		name="extract",
		status=CaptureStatus.EXTRACTING,
		version=lambda ctx: ctx.extractor.version,
		run=_extract,
		applies=lambda c: True,
	),
	Stage(
		name="people",
		# Still "extracting" to anyone watching: reading who a note is about is
		# part of reading the note, and the app's labels need no new state.
		status=CaptureStatus.EXTRACTING,
		version=lambda ctx: ctx.people.version,
		run=_people,
		applies=lambda c: True,
		optional=True,
	),
	Stage(
		name="faith",
		status=CaptureStatus.EXTRACTING,
		version=lambda ctx: ctx.faith.version,
		run=_faith,
		applies=lambda c: True,
		optional=True,
	),
	Stage(
		name="habits",
		status=CaptureStatus.EXTRACTING,
		version=lambda ctx: ctx.habits.version,
		run=_habits,
		applies=lambda c: True,
		optional=True,
	),
	Stage(
		name="scripture",
		status=CaptureStatus.EXTRACTING,
		version=lambda ctx: SCRIPTURE_VERSION,
		run=_scripture,
		applies=lambda c: True,
		optional=True,
	),
	Stage(
		name="embed",
		status=CaptureStatus.EMBEDDING,
		version=lambda ctx: f"{CHUNKER_VERSION}/{ctx.embedder.model}",
		run=_embed,
		applies=lambda c: True,
	),
	Stage(
		name="reconcile",
		# Still "embedding" to anyone watching; the note is already searchable.
		status=CaptureStatus.EMBEDDING,
		version=lambda ctx: ctx.reconciler.version,
		run=_reconcile,
		applies=lambda c: True,
		optional=True,
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


async def _set_aside(
	session: AsyncSession, run_id: uuid.UUID, exc: RetryableError | TerminalError
) -> None:
	"""An optional stage that failed. A terminal failure is a failed attempt on the
	ledger, and the backlog tries again up to people.BACKLOG_MAX_ATTEMPTS. A transient
	one (a 429, an outage) is not an attempt at all: the row is removed, so the note is
	simply still unread and the next backlog sweep picks it up."""
	if isinstance(exc, RetryableError):
		await session.execute(
			text("DELETE FROM job_runs WHERE id = :id"), {"id": run_id}
		)
	else:
		await _finish_run(session, run_id, "failed", repr(exc))


async def _forget_run(session: AsyncSession, run_id: uuid.UUID) -> None:
	await session.execute(text("DELETE FROM job_runs WHERE id = :id"), {"id": run_id})


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
			try:
				await stage.run(ctx, capture)
			except StageOff:
				await _forget_run(session, run_id)
				await commit_and_rebind(session, user_id)
				continue
			except (RetryableError, TerminalError) as exc:
				if not stage.optional:
					raise  # to the handlers below: this note fails or retries
				await _set_aside(session, run_id, exc)
				await commit_and_rebind(session, user_id)
				log.warning(
					"stage.optional_failed",
					capture_id=str(capture.id),
					stage=stage.name,
					error=repr(exc),
				)
				continue
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


async def read_people_in_old_note(
	ctx: PipelineContext, capture_id: uuid.UUID, user_id: uuid.UUID
) -> str:
	"""Run the people stage over a note that was ready before the stage existed, then
	index its mentions. The same ledger row (capture, "people", version) as the
	pipeline, so a note is read once however it gets there.

	Failures are set aside the same way as in the pipeline (`_set_aside`): a terminal
	one is a failed attempt, retried up to people.BACKLOG_MAX_ATTEMPTS; a transient one
	is not an attempt and returns "retry_later", which stops the sweep, since a
	provider that is down for one note is down for the next forty. A bug is logged and
	counted as a failed attempt rather than aborting the batch. The note itself is
	untouched: it stays ready and searchable whatever happens here.

	Returns what happened: missing | done | read | failed | retry_later."""
	session = ctx.session
	await bind_user(session, user_id)
	capture = await session.scalar(
		select(Capture).where(Capture.id == capture_id, Capture.user_id == user_id)
	)
	if capture is None or capture.status != CaptureStatus.READY.value:
		return "missing"
	stage = next(s for s in STAGES if s.name == "people")
	run_id = await _claim_run(session, capture, stage, stage.version(ctx))
	await commit_and_rebind(session, user_id)
	if run_id is None:
		return "done"
	try:
		await _people(ctx, capture)
	except (RetryableError, TerminalError) as exc:
		# Raised by the read, before anything was written but the paid-for calls.
		await _set_aside(session, run_id, exc)
		await commit_and_rebind(session, user_id)
		log.warning(
			"people.backlog_failed", capture_id=str(capture_id), error=repr(exc)
		)
		return "retry_later" if isinstance(exc, RetryableError) else "failed"
	except Exception as exc:
		log.exception("people.backlog_crashed", capture_id=str(capture_id))
		await session.rollback()
		await bind_user(session, user_id)
		await _finish_run(session, run_id, "failed", repr(exc))
		await commit_and_rebind(session, user_id)
		return "failed"
	await _finish_run(session, run_id, "succeeded")
	await commit_and_rebind(session, user_id)

	# Best-effort, like any derivation's re-embed (ADR 0021): until a mention has its
	# chunk, Ask still sees it live through its note.
	mentions = (
		await session.scalars(
			select(Derivation.id).where(
				Derivation.capture_id == capture.id,
				Derivation.user_id == user_id,
				Derivation.kind == "mention",
				Derivation.status != "rejected",
			)
		)
	).all()
	for mention_id in mentions:
		try:
			await reembed_derivation(session, ctx.embedder, mention_id, user_id)
			await commit_and_rebind(session, user_id)
		except (RetryableError, TerminalError) as exc:
			await session.rollback()
			await bind_user(session, user_id)
			log.warning(
				"people.backlog_embed_failed",
				derivation_id=str(mention_id),
				error=repr(exc),
			)
	return "read"


async def read_habits_in_old_note(
	ctx: PipelineContext, capture_id: uuid.UUID, user_id: uuid.UUID
) -> str:
	"""Run the habits stage over a note written before Habits was turned on (ADR
	0043), so a habit accepted today comes with the days memo already knew about. The
	same ledger row (capture, "habits", version) as the pipeline, so a note is read
	once however it gets there. Failures are set aside as for people: a transient one
	returns "retry_later" and stops the sweep.

	Returns: missing | off | done | read | failed | retry_later."""
	session = ctx.session
	await bind_user(session, user_id)
	capture = await session.scalar(
		select(Capture).where(Capture.id == capture_id, Capture.user_id == user_id)
	)
	if capture is None or capture.status != CaptureStatus.READY.value:
		return "missing"
	stage = next(s for s in STAGES if s.name == "habits")
	run_id = await _claim_run(session, capture, stage, stage.version(ctx))
	await commit_and_rebind(session, user_id)
	if run_id is None:
		return "done"
	try:
		await _habits(ctx, capture)
	except StageOff:  # turned off since the sweep picked the note
		await _forget_run(session, run_id)
		await commit_and_rebind(session, user_id)
		return "off"
	except (RetryableError, TerminalError) as exc:
		await _set_aside(session, run_id, exc)
		await commit_and_rebind(session, user_id)
		log.warning(
			"habits.backlog_failed", capture_id=str(capture_id), error=repr(exc)
		)
		return "retry_later" if isinstance(exc, RetryableError) else "failed"
	except Exception as exc:
		log.exception("habits.backlog_crashed", capture_id=str(capture_id))
		await session.rollback()
		await bind_user(session, user_id)
		await _finish_run(session, run_id, "failed", repr(exc))
		await commit_and_rebind(session, user_id)
		return "failed"
	await _finish_run(session, run_id, "succeeded")
	await commit_and_rebind(session, user_id)
	return "read"
