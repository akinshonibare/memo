"""Per-user fixed-window rate limits in Redis (issue #48).

Redis-backed so the limit holds across API instances. The interesting decision is what
happens when Redis is down: the ask limiter fails **closed** (a bill guard that fails
open is not a guard), the capture limiter fails **open** (durability outranks limiting;
losing a capture is worse than admitting a burst). Both are explicit at the call site.
"""

import math
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import structlog

log = structlog.get_logger("memo.ratelimit")


class RedisLike(Protocol):
	async def incr(self, name: str) -> Any: ...

	async def expire(self, name: str, time: int) -> Any: ...


@dataclass(frozen=True)
class Decision:
	allowed: bool
	retry_after: int  # seconds until the window resets; 0 when allowed
	remaining: int


async def check(
	redis: RedisLike,
	*,
	key: str,
	limit: int,
	window_seconds: int,
	on_error: Literal["open", "closed"],
	now: float | None = None,
) -> Decision:
	now = now if now is not None else time.time()
	window = int(now // window_seconds)
	name = f"ratelimit:{key}:{window}"
	reset_in = max(1, math.ceil((window + 1) * window_seconds - now))
	try:
		count = int(await redis.incr(name))
		if count == 1:
			await redis.expire(name, window_seconds + 1)
	except Exception as exc:
		log.warning(
			"ratelimit.redis_unavailable", key=key, policy=on_error, error=repr(exc)
		)
		if on_error == "open":
			return Decision(allowed=True, retry_after=0, remaining=limit)
		return Decision(allowed=False, retry_after=reset_in, remaining=0)
	if count > limit:
		return Decision(allowed=False, retry_after=reset_in, remaining=0)
	return Decision(allowed=True, retry_after=0, remaining=limit - count)
