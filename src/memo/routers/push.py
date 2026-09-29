"""The phone's side of the morning push (ADR 0047): register this phone, forget it,
and say what happened to a push - opened, or a thumbs up or down."""

import datetime as dt
import re
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select

from memo.auth import BearerToken, CurrentUser
from memo.db import DbSession
from memo.models import Push
from memo.services.push import NotSignedIn, forget_token, register_token

router = APIRouter(tags=["push"])

_EXPO_TOKEN = re.compile(r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]{10,120}\]$")


class TokenIn(BaseModel):
	model_config = ConfigDict(extra="forbid")

	token: str = Field(max_length=200)

	@field_validator("token")
	@classmethod
	def _is_an_expo_token(cls, value: str) -> str:
		if not _EXPO_TOKEN.match(value):
			raise ValueError("not an Expo push token")
		return value


@router.put("/push/token", status_code=status.HTTP_204_NO_CONTENT)
async def put_token(
	body: TokenIn, request: Request, user_id: CurrentUser, raw: BearerToken
) -> None:
	"""This phone may receive pushes, for as long as this session is signed in."""
	try:
		await register_token(
			request.app.state.session_factory, user_id, raw, body.token
		)
	except NotSignedIn as exc:
		raise HTTPException(status.HTTP_401_UNAUTHORIZED, "not signed in") from exc


@router.delete("/push/token", status_code=status.HTTP_204_NO_CONTENT)
async def delete_token(body: TokenIn, session: DbSession, user_id: CurrentUser) -> None:
	await forget_token(session, user_id, body.token)


class PushOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	kind: str
	local_date: dt.date
	status: str
	reason: str | None
	title: str | None
	body: str | None
	sent_at: dt.datetime | None
	opened_at: dt.datetime | None
	feedback: str | None


class PushesOut(BaseModel):
	"""The last month of pushes, and the number the brief says to watch: of the pushes
	that reached a phone, how many were acted on (opened). Target: 30% or better."""

	pushes: list[PushOut]
	sent: int
	opened: int
	act_on_rate: float | None
	up: int
	down: int


@router.get("/pushes", response_model=PushesOut)
async def list_pushes(session: DbSession, user_id: CurrentUser) -> PushesOut:
	since = dt.date.today() - dt.timedelta(days=30)
	rows = (
		await session.scalars(
			select(Push)
			.where(Push.user_id == user_id, Push.local_date >= since)
			.order_by(Push.local_date.desc())
		)
	).all()
	sent = [p for p in rows if p.status == "sent"]
	opened = sum(1 for p in sent if p.opened_at is not None)
	return PushesOut(
		pushes=[PushOut.model_validate(p) for p in rows],
		sent=len(sent),
		opened=opened,
		act_on_rate=round(opened / len(sent), 2) if sent else None,
		up=sum(1 for p in rows if p.feedback == "up"),
		down=sum(1 for p in rows if p.feedback == "down"),
	)


async def _own(session: DbSession, user_id: uuid.UUID, push_id: uuid.UUID) -> Push:
	push = await session.scalar(
		select(Push).where(Push.id == push_id, Push.user_id == user_id)
	)
	if push is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "push not found")
	return push


@router.post("/pushes/{push_id}/opened", status_code=status.HTTP_204_NO_CONTENT)
async def opened(push_id: uuid.UUID, session: DbSession, user_id: CurrentUser) -> None:
	"""The push was tapped. The first time counts; later taps change nothing."""
	push = await _own(session, user_id, push_id)
	if push.opened_at is None:
		push.opened_at = func.now()


class FeedbackIn(BaseModel):
	model_config = ConfigDict(extra="forbid")

	vote: Literal["up", "down"]


@router.post("/pushes/{push_id}/feedback", status_code=status.HTTP_204_NO_CONTENT)
async def feedback(
	push_id: uuid.UUID, body: FeedbackIn, session: DbSession, user_id: CurrentUser
) -> None:
	"""👍 or 👎 from the notification's buttons; the latest vote stands. A vote means
	the push was read, so it counts as opened too."""
	push = await _own(session, user_id, push_id)
	push.feedback = body.vote
	push.feedback_at = func.now()
	if push.opened_at is None:
		push.opened_at = func.now()
