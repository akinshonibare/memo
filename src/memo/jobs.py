"""arq task adapters. Each is a few lines that unpack `ctx` and call a service."""

import datetime as dt
import uuid
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select, text

from memo.db import bind_system, bind_user
from memo.models import Capture, Recording
from memo.services.derivation_index import reembed_derivation, refresh_stale_chunks
from memo.services.habits import unread_for_habits
from memo.services.mood import unread_for_mood
from memo.services.morning import build_morning_view, is_build_hour
from memo.services.people import unread_notes
from memo.services.pipeline import (
	PipelineContext,
	process_capture,
	read_habits_in_old_note,
	read_mood_in_old_note,
	read_people_in_old_note,
)
from memo.services.push import check_receipts, send_due_mornings
from memo.services.queue import PROCESS_CAPTURE
from memo.services.retry import RetryableError, TerminalError, backoff_delay

log = structlog.get_logger("memo.jobs")


async def process_capture_task(
	ctx: dict[str, Any], capture_id: str, user_id: str, request_id: str | None = None
) -> str:
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(
		request_id=request_id or ctx.get("job_id"),
		job_id=ctx.get("job_id"),
		job_try=ctx.get("job_try"),
	)
	async with ctx["session_factory"]() as session:
		pipeline = PipelineContext(
			session=session,
			transcriber=ctx["transcriber"],
			object_store=ctx["object_store"],
			extractor=ctx["extractor"],
			embedder=ctx["embedder"],
			people=ctx["people"],
			max_attempts=ctx["settings"].max_attempts,
			describer=ctx["describer"],
			faith=ctx["faith"],
			habits=ctx["habits"],
			mood=ctx["mood"],
			reconciler=ctx["reconciler"],
		)
		outcome = await process_capture(
			pipeline, uuid.UUID(capture_id), uuid.UUID(user_id)
		)
	if outcome.retry_after is not None:
		raise Retry(defer=outcome.retry_after)
	return outcome.status


EMBED_MAX_TRIES = 6


async def embed_derivation_task(
	ctx: dict[str, Any], derivation_id: str, user_id: str, request_id: str | None = None
) -> str:
	"""Keep a derivation's chunk current after an add or an edit (ADR 0021).

	Best-effort by design: until it runs, Ask still sees the derivation through its
	note (read live at answer time), so a failure costs recall on questions that only
	match the item itself - worth a few retries, not a dead-letter state."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(
		request_id=request_id or ctx.get("job_id"), job_id=ctx.get("job_id")
	)
	tries = int(ctx.get("job_try") or 1)
	uid = uuid.UUID(user_id)
	try:
		async with ctx["session_factory"]() as session:
			await bind_user(session, uid)
			outcome = await reembed_derivation(
				session, ctx["embedder"], uuid.UUID(derivation_id), uid
			)
			await session.commit()
	except RetryableError as exc:
		if tries >= EMBED_MAX_TRIES:
			log.warning("derivation.reembed_gave_up", error=str(exc), tries=tries)
			return "gave_up"
		raise Retry(defer=backoff_delay(tries)) from exc
	except TerminalError as exc:
		log.warning("derivation.reembed_failed", error=str(exc))
		return "failed"
	return outcome


async def sweep_unclaimed_recordings_task(ctx: dict[str, Any]) -> int:
	"""Delete recordings that were presigned but never attached to a capture.

	Railway Buckets have no lifecycle rules, so this cron is the expiry policy.
	"""
	settings = ctx["settings"]
	cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
		hours=settings.unclaimed_recording_ttl_hours
	)
	removed = 0
	async with ctx["session_factory"]() as session:
		await bind_system(session)
		orphans = (
			await session.scalars(
				select(Recording)
				.outerjoin(Capture, Capture.recording_id == Recording.id)
				.where(Capture.id.is_(None), Recording.created_at < cutoff)
			)
		).all()
		for recording in orphans:
			await ctx["object_store"].delete(recording.storage_key)
			await session.delete(recording)
			removed += 1
		await session.commit()
	log.info("sweep.unclaimed_recordings", removed=removed)
	return removed


async def build_morning_views_task(
	ctx: dict[str, Any], *, now: dt.datetime | None = None
) -> int:
	"""Hourly: build the view for every user whose local clock is in the build hour.

	"Nightly" is harder than it sounds: users are in different timezones and the
	clock moves twice a year. Asking per user "is it 4am where you are, right now?"
	every hour is what makes that a non-problem - a DST shift just changes which UTC
	hour the answer is yes.
	"""
	now = now or dt.datetime.now(dt.UTC)
	async with ctx["session_factory"]() as session:
		await bind_system(session)
		users = (await session.execute(text("SELECT id, timezone FROM users"))).all()
	built = 0
	for user_id, timezone in users:
		if not is_build_hour(now, timezone):
			continue
		async with ctx["session_factory"]() as session:
			await bind_user(session, user_id)
			await build_morning_view(session, user_id, now=now)
			await session.commit()
		built += 1
	log.info("morning.sweep", users=len(users), built=built)
	return built


async def requeue_stale_captures_task(ctx: dict[str, Any]) -> int:
	"""Re-enqueue captures stuck in `pending` (ADR 0012: Postgres is authoritative)."""
	settings = ctx["settings"]
	cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(
		minutes=settings.stale_pending_minutes
	)
	async with ctx["session_factory"]() as session:
		await bind_system(session)
		rows = (
			await session.execute(
				text(
					"SELECT id, user_id FROM captures "
					"WHERE status = 'pending' AND created_at < :cutoff"
				),
				{"cutoff": cutoff},
			)
		).all()
	for capture_id, user_id in rows:
		await ctx["redis"].enqueue_job(
			PROCESS_CAPTURE,
			str(capture_id),
			str(user_id),
			request_id=None,
			_job_id=f"process_capture:{capture_id}",
		)
	log.info("sweep.stale_pending", requeued=len(rows))
	return len(rows)


PEOPLE_BACKLOG_BATCH = 40


async def read_people_backlog_task(ctx: dict[str, Any]) -> int:
	"""Read people in notes written before the people stage existed (ADR 0029).

	A sweep rather than a one-off script: it needs no terminal session to run, it
	resumes after a deploy or a crash where it left off (the job_runs ledger is the
	bookmark), and once the backlog is empty it costs one query every few minutes.
	Newest notes first, since recent contact is what the reminders depend on."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
	async with ctx["session_factory"]() as session:
		await bind_system(session)
		backlog = await unread_notes(session, limit=PEOPLE_BACKLOG_BATCH)
	outcomes: dict[str, int] = {}
	for capture_id, user_id in backlog:
		async with ctx["session_factory"]() as session:
			pipeline = PipelineContext(
				session=session,
				transcriber=ctx["transcriber"],
				object_store=ctx["object_store"],
				extractor=ctx["extractor"],
				embedder=ctx["embedder"],
				people=ctx["people"],
			)
			outcome = await read_people_in_old_note(pipeline, capture_id, user_id)
		outcomes[outcome] = outcomes.get(outcome, 0) + 1
		if outcome == "retry_later":
			break  # the provider is struggling: the next sweep, not the next note
	log.info("sweep.people_backlog", notes=len(backlog), **outcomes)
	return len(backlog)


async def refresh_derivation_chunks_task(ctx: dict[str, Any]) -> int:
	"""Bring stale item chunks up to date, a batch per hour (ADR 0045): the items
	embedded before their dates were part of their text, and any future change to
	that text. A sweep, like the people backlog; a provider hiccup waits for the next
	hour rather than retrying, since nothing the user sees depends on it."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
	try:
		async with ctx["session_factory"]() as session:
			await bind_system(session)
			refreshed = await refresh_stale_chunks(session, ctx["embedder"])
			await session.commit()
	except (RetryableError, TerminalError) as exc:
		log.warning("sweep.derivation_chunks_failed", error=str(exc))
		return 0
	log.info("sweep.derivation_chunks", refreshed=refreshed)
	return refreshed


async def send_morning_pushes_task(ctx: dict[str, Any]) -> dict[str, int]:
	"""Every five minutes: send the morning push to everyone whose time has come
	(ADR 0047). Once a day each, claimed before sending."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
	outcomes = await send_due_mornings(ctx["session_factory"], ctx["push_sender"])
	if outcomes:
		log.info("sweep.morning_pushes", **outcomes)
	return outcomes


async def check_push_receipts_task(ctx: dict[str, Any]) -> int:
	"""Expo's receipts for pushes sent 15 minutes to a day ago: tokens Apple says are
	gone get disabled. A provider hiccup waits for the next run."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
	try:
		return await check_receipts(ctx["session_factory"], ctx["push_sender"])
	except (RetryableError, TerminalError) as exc:
		log.warning("sweep.push_receipts_failed", error=str(exc))
		return 0


HABITS_BACKLOG_BATCH = 40


async def read_habits_backlog_task(ctx: dict[str, Any]) -> int:
	"""Read the last 60 days of notes for habits, once Habits is turned on (ADR 0043),
	so accepting "Gym" shows the gym days memo already knew about. A sweep like the
	people backlog: the job_runs ledger is the bookmark, and an empty backlog costs one
	query."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
	async with ctx["session_factory"]() as session:
		await bind_system(session)
		backlog = await unread_for_habits(session, limit=HABITS_BACKLOG_BATCH)
	outcomes: dict[str, int] = {}
	for capture_id, user_id in backlog:
		async with ctx["session_factory"]() as session:
			pipeline = PipelineContext(
				session=session,
				transcriber=ctx["transcriber"],
				object_store=ctx["object_store"],
				extractor=ctx["extractor"],
				embedder=ctx["embedder"],
				people=ctx["people"],
				habits=ctx["habits"],
			)
			outcome = await read_habits_in_old_note(pipeline, capture_id, user_id)
		outcomes[outcome] = outcomes.get(outcome, 0) + 1
		if outcome == "retry_later":
			break
	log.info("sweep.habits_backlog", notes=len(backlog), **outcomes)
	return len(backlog)


MOOD_BACKLOG_BATCH = 40


async def read_mood_backlog_task(ctx: dict[str, Any]) -> int:
	"""Read the last 30 days of notes for mood, once Within is turned on (ADR 0058),
	so the Within screen opens on the weeks memo already has. A sweep like the habits
	backlog: the job_runs ledger is the bookmark, and an empty backlog costs one
	query."""
	structlog.contextvars.clear_contextvars()
	structlog.contextvars.bind_contextvars(job_id=ctx.get("job_id"))
	async with ctx["session_factory"]() as session:
		await bind_system(session)
		backlog = await unread_for_mood(session, limit=MOOD_BACKLOG_BATCH)
	outcomes: dict[str, int] = {}
	for capture_id, user_id in backlog:
		async with ctx["session_factory"]() as session:
			pipeline = PipelineContext(
				session=session,
				transcriber=ctx["transcriber"],
				object_store=ctx["object_store"],
				extractor=ctx["extractor"],
				embedder=ctx["embedder"],
				people=ctx["people"],
				mood=ctx["mood"],
			)
			outcome = await read_mood_in_old_note(pipeline, capture_id, user_id)
		outcomes[outcome] = outcomes.get(outcome, 0) + 1
		if outcome == "retry_later":
			break
	log.info("sweep.mood_backlog", notes=len(backlog), **outcomes)
	return len(backlog)
