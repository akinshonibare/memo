"""FastAPI dependencies that apply the rate limits to the expensive endpoints."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Response, status

from memo.auth import CurrentUser
from memo.config import Settings, get_settings
from memo.services.ratelimit import check


def _limiter(
	name: str, on_error: Literal["open", "closed"]
) -> Callable[..., Awaitable[None]]:
	async def dependency(
		request: Request,
		response: Response,
		user_id: CurrentUser,
		settings: Annotated[Settings, Depends(get_settings)],
	) -> None:
		limit: int = getattr(settings, f"rate_{name}_per_minute")
		decision = await check(
			request.app.state.redis,
			key=f"{name}:{user_id}",
			limit=limit,
			window_seconds=60,
			on_error=on_error,
		)
		headers = {
			"X-RateLimit-Limit": str(limit),
			"X-RateLimit-Remaining": str(decision.remaining),
		}
		if not decision.allowed:
			raise HTTPException(
				status.HTTP_429_TOO_MANY_REQUESTS,
				f"rate limit: {limit} {name} requests per minute",
				headers={**headers, "Retry-After": str(decision.retry_after)},
			)
		response.headers.update(headers)

	return dependency


# Fail closed: an unbounded ask is an unbounded bill.
limit_ask = _limiter("ask", "closed")
# Fail open: a capture is the one thing memo must never refuse for its own reasons.
limit_capture = _limiter("capture", "open")
