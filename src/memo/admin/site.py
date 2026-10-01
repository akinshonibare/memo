"""The operator's live cost page (ADR 0064): every user's AI spend against their daily
limit, served by the API at /admin/costs behind an operator password.

HTTP Basic, because every browser (a phone's too) already knows how to ask for it and
keep it for the session, and it travels in a header, never in a URL a log would keep.
The password is OPERATOR_TOKEN, set only in Railway; unset, these routes are a 404.
Wrong passwords are rate-limited by address, failing closed. Read-only, never cached,
never indexed.
"""

import base64
import binascii
import datetime as dt
import json
import secrets
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse

from memo.admin.costs import as_json, daily_totals, stage_costs, user_costs
from memo.config import Settings, get_settings
from memo.db import bind_system
from memo.services.ratelimit import check

router = APIRouter(prefix="/admin", include_in_schema=False)

PAGE = (Path(__file__).with_name("costs_page.html")).read_text()
FAILED_PER_MINUTE = 10
HEADERS = {
	"Cache-Control": "no-store",
	"X-Robots-Tag": "noindex, nofollow",
	"Referrer-Policy": "no-referrer",
	"X-Content-Type-Options": "nosniff",
	"X-Frame-Options": "DENY",
	"Content-Security-Policy": (
		"default-src 'none'; script-src 'unsafe-inline'; "
		"style-src 'unsafe-inline' https://fonts.googleapis.com; "
		"font-src https://fonts.gstatic.com; img-src data:; base-uri 'none'; "
		"form-action 'none'; frame-ancestors 'none'"
	),
}
_CHALLENGE = {"WWW-Authenticate": 'Basic realm="memo operator", charset="UTF-8"'}


def _address(request: Request) -> str:
	# Railway's proxy puts the caller first in X-Forwarded-For.
	forwarded = request.headers.get("x-forwarded-for", "")
	if forwarded:
		return forwarded.split(",")[0].strip()
	return request.client.host if request.client else "unknown"


def _password(request: Request) -> str | None:
	header = request.headers.get("authorization", "")
	scheme, _, value = header.partition(" ")
	if scheme.lower() != "basic" or not value:
		return None
	try:
		decoded = base64.b64decode(value, validate=True).decode("utf-8")
	except (binascii.Error, UnicodeDecodeError):
		return None
	_, sep, password = decoded.partition(":")
	return password if sep else None


async def operator(
	request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> None:
	"""Let the operator through; anyone else gets a 401 that asks for the password,
	or a 429 after too many wrong ones. No token configured: the page isn't there."""
	if settings.operator_token is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	given = _password(request)
	expected = settings.operator_token.get_secret_value()
	if given is not None and secrets.compare_digest(
		given.encode("utf-8"), expected.encode("utf-8")
	):
		return
	if given is not None:
		decision = await check(
			request.app.state.redis,
			key=f"admin:{_address(request)}",
			limit=FAILED_PER_MINUTE,
			window_seconds=60,
			on_error="closed",
		)
		if not decision.allowed:
			raise HTTPException(
				status.HTTP_429_TOO_MANY_REQUESTS,
				"too many wrong passwords; try again in a minute",
				headers={"Retry-After": str(decision.retry_after)},
			)
	raise HTTPException(status.HTTP_401_UNAUTHORIZED, headers=_CHALLENGE)


async def _data(request: Request, days: int) -> str:
	now = dt.datetime.now(dt.UTC)
	async with request.app.state.session_factory() as session, session.begin():
		await bind_system(session)
		users = await user_costs(session, days=days, now=now)
		stages = await stage_costs(session, days=days, now=now)
		daily = await daily_totals(session, days=max(days, 14), now=now)
	return as_json(users, stages, daily, days=days, now=now)


Days = Annotated[int, Query(ge=1, le=90)]


@router.get("/costs", dependencies=[Depends(operator)])
async def costs_page(request: Request, days: Days = 30) -> HTMLResponse:
	data = await _data(request, days)
	# Inside a <script>: a name containing "</script>" must not end it.
	html = PAGE.replace("__DATA__", data.replace("</", "<\\/"))
	return HTMLResponse(html, headers=HEADERS)


@router.get("/costs.json", dependencies=[Depends(operator)])
async def costs_json(request: Request, days: Days = 30) -> JSONResponse:
	return JSONResponse(json.loads(await _data(request, days)), headers=HEADERS)
