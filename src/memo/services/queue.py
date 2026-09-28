import uuid
from typing import Protocol

import structlog
from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from memo.config import Settings

log = structlog.get_logger("memo.queue")

PROCESS_CAPTURE = "process_capture_task"
EMBED_DERIVATION = "embed_derivation_task"


class Queue(Protocol):
	async def enqueue_process_capture(
		self, capture_id: uuid.UUID, user_id: uuid.UUID, request_id: str | None
	) -> None: ...

	async def enqueue_embed_derivation(
		self,
		derivation_id: uuid.UUID,
		user_id: uuid.UUID,
		fingerprint: str,
		request_id: str | None,
	) -> None: ...


class ArqQueue:
	def __init__(self, pool: ArqRedis) -> None:
		self._pool = pool

	async def enqueue_process_capture(
		self, capture_id: uuid.UUID, user_id: uuid.UUID, request_id: str | None
	) -> None:
		# A deterministic job id means a duplicate enqueue while the job is queued or
		# running is dropped by arq. The database ledger (job_runs) is the real guard.
		job = await self._pool.enqueue_job(
			PROCESS_CAPTURE,
			str(capture_id),
			str(user_id),
			request_id=request_id,
			_job_id=f"process_capture:{capture_id}",
		)
		log.info(
			"enqueued",
			job=PROCESS_CAPTURE,
			capture_id=str(capture_id),
			deduplicated=job is None,
		)

	async def enqueue_embed_derivation(
		self,
		derivation_id: uuid.UUID,
		user_id: uuid.UUID,
		fingerprint: str,
		request_id: str | None,
	) -> None:
		# The fingerprint of the text to embed is in the id: a second edit while the
		# first job runs is new content, so it must not be deduplicated away.
		job = await self._pool.enqueue_job(
			EMBED_DERIVATION,
			str(derivation_id),
			str(user_id),
			request_id=request_id,
			_job_id=f"embed_derivation:{derivation_id}:{fingerprint}",
		)
		log.info(
			"enqueued",
			job=EMBED_DERIVATION,
			derivation_id=str(derivation_id),
			deduplicated=job is None,
		)

	async def aclose(self) -> None:
		await self._pool.aclose()


async def make_queue(settings: Settings) -> ArqQueue:
	pool = await create_pool(
		RedisSettings.from_dsn(settings.redis_url),
		default_queue_name=settings.arq_queue_name,
	)
	return ArqQueue(pool)
