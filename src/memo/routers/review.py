"""The weekly review (ADR 0028; the brief's V3): Monday to Sunday in the user's zone.

Deliberately built from queries, not a model: what was captured, what got done, what
is still open, what is coming, and the new ideas. A written "this week you..." summary
would read nicer and cost a model call per view with nothing to check it against; the
facts come first, and a summary can be added once there is an eval for it.

No guilt mechanics (brief): still-open todos are listed, never counted against you.
"""

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.models import Capture, Derivation, User
from memo.schemas.derivations import DerivationOut
from memo.schemas.faith import FaithWeek
from memo.services import faith
from memo.services.morning import tz_or_utc

router = APIRouter(prefix="/review", tags=["review"])

OPEN_CAP = 12


class Captured(BaseModel):
	notes: int
	voice: int
	photos: int


class WeekOut(BaseModel):
	start: dt.date  # Monday
	end: dt.date  # Sunday
	captured: Captured
	done: list[DerivationOut]
	still_open: list[DerivationOut]
	still_open_total: int
	coming_up: list[DerivationOut]  # events in the next 7 days
	new_ideas: list[DerivationOut]
	# Its own section at the end, only while Faith is on (ADR 0042). None otherwise,
	# and absent to an app built before it.
	faith: FaithWeek | None = None


@router.get("/week", response_model=WeekOut)
async def week(
	session: DbSession,
	user_id: CurrentUser,
	weeks_ago: Annotated[int, Query(ge=0, le=52)] = 0,
) -> WeekOut:
	user = await session.get(User, user_id)
	zone = tz_or_utc(user.timezone if user is not None else "UTC")
	now = dt.datetime.now(zone)
	monday = now.date() - dt.timedelta(days=now.weekday() + 7 * weeks_ago)
	sunday = monday + dt.timedelta(days=6)
	since = dt.datetime.combine(monday, dt.time.min, zone)
	until = since + dt.timedelta(days=7)

	kinds = dict(
		(
			await session.execute(
				select(Capture.kind, func.count())
				.where(
					Capture.user_id == user_id,
					Capture.created_at >= since,
					Capture.created_at < until,
				)
				.group_by(Capture.kind)
			)
		).all()
	)

	live = select(Derivation).where(
		Derivation.user_id == user_id, Derivation.status != "rejected"
	)
	done = (
		await session.scalars(
			live.where(
				Derivation.kind == "todo",
				Derivation.completed_at >= since,
				Derivation.completed_at < until,
			).order_by(Derivation.completed_at)
		)
	).all()
	open_todos = (
		await session.scalars(
			live.where(Derivation.kind == "todo", Derivation.completed_at.is_(None))
		)
	).all()
	# Dated first, soonest first; undated after, newest first.
	open_sorted = sorted(
		open_todos,
		key=lambda d: (
			d.payload.get("due") is None,
			d.payload.get("due") or "",
			-d.created_at.timestamp(),
		),
	)
	horizon = dt.datetime.now(dt.UTC) + dt.timedelta(days=7)
	upcoming = [
		d
		for d in (await session.scalars(live.where(Derivation.kind == "event"))).all()
		if d.payload.get("starts_at")
		and dt.datetime.now(dt.UTC)
		<= dt.datetime.fromisoformat(d.payload["starts_at"])
		<= horizon
	]
	upcoming.sort(key=lambda d: d.payload["starts_at"])
	ideas = (
		await session.scalars(
			live.where(
				Derivation.kind == "idea",
				Derivation.created_at >= since,
				Derivation.created_at < until,
			).order_by(Derivation.created_at.desc())
		)
	).all()

	def out(rows: list[Derivation]) -> list[DerivationOut]:
		return [DerivationOut.model_validate(d) for d in rows]

	return WeekOut(
		start=monday,
		end=sunday,
		captured=Captured(
			notes=int(kinds.get("text", 0)),
			voice=int(kinds.get("audio", 0)),
			photos=int(kinds.get("image", 0)),
		),
		done=out(list(done)),
		still_open=out(open_sorted[:OPEN_CAP]),
		still_open_total=len(open_sorted),
		coming_up=out(upcoming),
		new_ideas=out(list(ideas)),
		faith=(
			await faith.week(session, user_id, since=since, until=until)
			if faith.faith_hidden(user)
			else None
		),
	)
