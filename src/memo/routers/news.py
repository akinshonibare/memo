"""What's new in each of memo's places, for the Places button on Home: the newest
thing each one holds. The phone remembers when you last looked at each place and
marks the ones with something newer, so a todo taken from a note sends you to the
Planner without anyone counting unread items."""

import datetime as dt

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.models import Derivation, Person, User
from memo.schemas.derivations import ACTIVITY, KINDS, PASSAGE, PRAYER, UPDATE

router = APIRouter(tags=["news"])


class NewsOut(BaseModel):
	planner: dt.datetime | None  # the newest todo, event or idea, or idea carried on
	review: dt.datetime | None  # the newest item waiting for a look
	people: dt.datetime | None  # the newest person memo met
	faith: dt.datetime | None  # the newest prayer, passage or "answered?" (Faith on)
	habits: dt.datetime | None  # the newest day a habit counted


@router.get("/news", response_model=NewsOut)
async def news(session: DbSession, user_id: CurrentUser) -> NewsOut:
	newest = func.max(Derivation.created_at)
	mine = Derivation.user_id == user_id
	kept = Derivation.status != "rejected"
	target = aliased(Derivation)
	suggested = select(func.max(Derivation.created_at)).join(
		target,
		and_(target.id == Derivation.target_id, target.user_id == Derivation.user_id),
	)
	planner = await session.scalar(
		select(newest).where(mine, kept, Derivation.kind.in_(KINDS))
	)
	ideas_carried = await session.scalar(
		suggested.where(
			mine,
			Derivation.kind == UPDATE,
			Derivation.status == "pending_review",
			target.kind == "idea",
		)
	)
	review = await session.scalar(
		select(newest).where(
			mine, Derivation.status == "pending_review", Derivation.kind.in_(KINDS)
		)
	)
	people = await session.scalar(
		select(func.max(Person.created_at)).where(Person.user_id == user_id)
	)
	habits = await session.scalar(
		select(newest).where(mine, kept, Derivation.kind == ACTIVITY)
	)
	user = await session.get(User, user_id)
	faith = None
	if user is not None and user.faith_enabled:
		faith_items = await session.scalar(
			select(newest).where(mine, kept, Derivation.kind.in_((PRAYER, PASSAGE)))
		)
		answered = await session.scalar(
			suggested.where(
				mine,
				Derivation.kind == UPDATE,
				Derivation.status == "pending_review",
				target.kind == PRAYER,
			)
		)
		faith = _latest(faith_items, answered)
	return NewsOut(
		planner=_latest(planner, ideas_carried),
		review=review,
		people=people,
		faith=faith,
		habits=habits,
	)


def _latest(*moments: dt.datetime | None) -> dt.datetime | None:
	known = [m for m in moments if m is not None]
	return max(known) if known else None
