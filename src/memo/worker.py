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
	process_capture_task,
	requeue_stale_captures_task,
	sweep_unclaimed_recordings_task,
)
from memo.logging import configure_logging
from memo.services.extraction import make_extractor
from memo.services.storage import S3ObjectStore
from memo.services.transcription import make_transcriber


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
	)


async def shutdown(ctx: dict[str, Any]) -> None:
	await ctx["http"].aclose()
	await ctx["engine"].dispose()


_settings = get_settings()


class WorkerSettings:
	functions = [
		process_capture_task,
		sweep_unclaimed_recordings_task,
		requeue_stale_captures_task,
	]
	cron_jobs = [
		cron(sweep_unclaimed_recordings_task, minute=7),
		cron(requeue_stale_captures_task, minute={3, 18, 33, 48}),
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
