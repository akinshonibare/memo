"""arq worker: `uv run arq memo.worker.WorkerSettings`.

Startup builds the same engine, session factory, HTTP client and logging config as
the API, so a job and a request are the same code with a different entry point.
"""

from typing import Any

import httpx
from arq import cron
from arq.connections import RedisSettings

from memo.config import get_settings
from memo.db import make_engine, make_session_factory
from memo.jobs import (
	build_morning_views_task,
	embed_derivation_task,
	process_capture_task,
	read_habits_backlog_task,
	read_people_backlog_task,
	requeue_stale_captures_task,
	sweep_unclaimed_recordings_task,
)
from memo.logging import configure_logging
from memo.services.embeddings import make_embedder
from memo.services.extraction import make_extractor
from memo.services.faith import make_faith_reader
from memo.services.habits import make_habits_reader
from memo.services.people import make_people_reader
from memo.services.storage import S3ObjectStore
from memo.services.transcription import make_transcriber
from memo.services.vision import make_describer


async def startup(ctx: dict[str, Any]) -> None:
	settings = get_settings()
	configure_logging(settings.log_level)
	engine = make_engine(settings.database_url)
	# One HTTP client for the worker's life: connection reuse is the "cold start"
	# concern a hosted STT has, and it is paid once here rather than per job.
	http = httpx.AsyncClient(timeout=httpx.Timeout(180.0, connect=10.0))
	ctx.update(
		settings=settings,
		engine=engine,
		session_factory=make_session_factory(engine),
		http=http,
		object_store=S3ObjectStore.from_settings(settings),
		transcriber=make_transcriber(settings, http),
		extractor=make_extractor(settings),
		people=make_people_reader(settings),
		faith=make_faith_reader(settings),
		habits=make_habits_reader(settings),
		describer=make_describer(settings),
		embedder=make_embedder(settings, http),
	)


async def shutdown(ctx: dict[str, Any]) -> None:
	await ctx["http"].aclose()
	await ctx["engine"].dispose()


_settings = get_settings()


class WorkerSettings:
	functions = [
		process_capture_task,
		embed_derivation_task,
		sweep_unclaimed_recordings_task,
		requeue_stale_captures_task,
		build_morning_views_task,
		read_people_backlog_task,
		read_habits_backlog_task,
	]
	cron_jobs = [
		cron(sweep_unclaimed_recordings_task, minute=7),
		cron(requeue_stale_captures_task, minute={3, 18, 33, 48}),
		# Hourly; each user is built in the hour that is 04:00 on their own clock.
		cron(build_morning_views_task, minute=5),
		# Old notes, a batch at a time, until none are left unread (ADR 0029).
		cron(read_people_backlog_task, minute={1, 11, 21, 31, 41, 51}),
		# Recent notes, once Habits is on, for the history a habit brings (ADR 0043).
		cron(read_habits_backlog_task, minute={6, 16, 26, 36, 46, 56}),
	]
	on_startup = startup
	on_shutdown = shutdown
	redis_settings = RedisSettings.from_dsn(_settings.redis_url)
	queue_name = _settings.arq_queue_name
	# The pipeline's attempt_count cap is the real limit (ADR 0012); arq's must be
	# high enough never to be the thing that gives up first.
	max_tries = 50
	job_timeout = 600
	# Results are never read from Redis, and keeping them would block re-enqueueing
	# the same deterministic job id after a user-triggered retry.
	keep_result = 0
