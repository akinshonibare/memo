"""arq task adapters. Each is a few lines that unpack `ctx` and call a service."""

import datetime as dt
import uuid
from typing import Any

import structlog
from arq import Retry
from sqlalchemy import select, text

from memo.db import bind_system, bind_user
from memo.models import Capture, Recording
from memo.services.derivation_index import reembed_derivation
from memo.services.morning import build_morning_view, is_build_hour
from memo.services.people import unread_notes
from memo.services.pipeline import (
	PipelineContext,
	process_capture,
	read_people_in_old_note,
)
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
