"""The morning view: today's todos and events, plus one resurfaced idea (issue #38).

Only the idea is a snapshot (ADR 0020). Picking it has a side effect - a resurfacing row
- and it must stay the same all day, so it is chosen once and pinned on the stored view.
Todos and events are read live on every request, so a review, an edit, or a delete
after the 04:00 build shows up immediately instead of tomorrow.

Push-side retrieval is what creates attachment (brief), and it is rationed on purpose:
one idea, never five. No guilt mechanics - nothing here is labelled overdue or counted
against the user; a todo whose day has passed is simply still on the list.
"""

import datetime as dt
import uuid
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from memo.ids import uuid7
from memo.models import Derivation, MorningView, Resurfacing, User

log = structlog.get_logger("memo.morning")

# Local hour at which each user's view is built. The cron runs hourly and asks, per
# user, "is it this hour where you are?" - so DST changes need no special handling.
BUILD_HOUR = 4
RESURFACE_MIN_AGE_DAYS = 3
RESURFACE_COOLDOWN_DAYS = 60
RECENT_WINDOW_DAYS = 7


class TodoItem(BaseModel):
	id: uuid.UUID
	capture_id: uuid.UUID | None
	title: str
	due: dt.date | None
	notes: str | None


class EventItem(BaseModel):
	id: uuid.UUID
	capture_id: uuid.UUID | None
	title: str
	starts_at: dt.datetime
	ends_at: dt.datetime | None
	location: str | None


class ResurfacedIdea(BaseModel):
	id: uuid.UUID
	capture_id: uuid.UUID | None
	title: str
	summary: str
	captured_on: dt.date


class MorningViewOut(BaseModel):
	local_date: dt.date
	timezone: str
	todos: list[TodoItem]
	events: list[EventItem]
	resurfaced_idea: ResurfacedIdea | None
	built_at: dt.datetime


def tz_or_utc(name: str) -> ZoneInfo:
	try:
		return ZoneInfo(name)
	except (KeyError, ValueError):
		return ZoneInfo("UTC")


def is_build_hour(now: dt.datetime, timezone: str) -> bool:
	return now.astimezone(tz_or_utc(timezone)).hour == BUILD_HOUR


_RESURFACE_SQL = text(
	"""
	WITH recent AS (
		SELECT avg(embedding) AS centroid
		FROM chunks
		WHERE user_id = :user_id AND derivation_id IS NULL
			AND superseded_at IS NULL
			AND created_at > CAST(:recent_since AS timestamptz)
	)
	SELECT d.id,
		ln(1 + extract(epoch FROM (CAST(:now AS timestamptz) - d.created_at))
			/ 86400.0)
		+ CASE WHEN r.centroid IS NULL OR k.embedding IS NULL THEN 0
			ELSE 2 * (1 - (k.embedding <=> r.centroid)) END AS score
	FROM derivations d
	LEFT JOIN chunks k
		ON k.derivation_id = d.id AND k.user_id = d.user_id AND k.superseded_at IS NULL
	CROSS JOIN recent r
	WHERE d.user_id = :user_id
		AND d.kind = 'idea'
		AND d.status IN ('confirmed', 'pending_review', 'user_created')
		AND d.created_at < CAST(:min_age_before AS timestamptz)
		AND NOT EXISTS (
			SELECT 1 FROM resurfacings s
			WHERE s.derivation_id = d.id AND s.user_id = d.user_id
				AND s.shown_on > CAST(:cooldown_since AS date)
		)
	ORDER BY score DESC, d.created_at ASC
	LIMIT 1
	"""
)


async def pick_resurfacing(
	session: AsyncSession, user_id: uuid.UUID, *, now: dt.datetime, today: dt.date
) -> Derivation | None:
	"""A ranking, not a lottery: older ideas score higher (log of age), ideas close to
	what the user has been writing about this week score higher (cosine to the recent
	centroid, when embeddings exist), and anything shown recently is out."""
	row = (
		await session.execute(
			_RESURFACE_SQL,
			{
				"user_id": user_id,
				"now": now,
				"recent_since": now - dt.timedelta(days=RECENT_WINDOW_DAYS),
				"min_age_before": now - dt.timedelta(days=RESURFACE_MIN_AGE_DAYS),
				"cooldown_since": today - dt.timedelta(days=RESURFACE_COOLDOWN_DAYS),
			},
		)
	).first()
	if row is None:
		return None
	return await session.get(Derivation, row[0])


def _todo(d: Derivation) -> TodoItem:
	p = d.payload
	return TodoItem(
		id=d.id,
		capture_id=d.capture_id,
		title=str(p.get("title", "")),
		due=dt.date.fromisoformat(p["due"]) if p.get("due") else None,
		notes=p.get("notes"),
	)


def _event(d: Derivation) -> EventItem:
	p = d.payload
	return EventItem(
		id=d.id,
		capture_id=d.capture_id,
		title=str(p.get("title", "")),
		starts_at=dt.datetime.fromisoformat(p["starts_at"]),
		ends_at=dt.datetime.fromisoformat(p["ends_at"]) if p.get("ends_at") else None,
		location=p.get("location"),
	)


async def _todays_items(
	session: AsyncSession, user_id: uuid.UUID, *, today: dt.date, zone: ZoneInfo
) -> tuple[list[TodoItem], list[EventItem]]:
	live = select(Derivation).where(
		Derivation.user_id == user_id, Derivation.status != "rejected"
	)
	todos = [
		_todo(d)
		for d in (await session.scalars(live.where(Derivation.kind == "todo"))).all()
		if d.payload.get("due") and dt.date.fromisoformat(d.payload["due"]) <= today
	]
	todos.sort(key=lambda t: (t.due or today, t.title))
	events = [
		_event(d)
		for d in (await session.scalars(live.where(Derivation.kind == "event"))).all()
		if d.payload.get("starts_at")
		and dt.datetime.fromisoformat(d.payload["starts_at"]).astimezone(zone).date()
		== today
	]
	events.sort(key=lambda e: e.starts_at)
	return todos, events


async def build_morning_view(
	session: AsyncSession, user_id: uuid.UUID, *, now: dt.datetime | None = None
) -> MorningViewOut:
	"""Build (or rebuild) today's view. A rebuild keeps the idea already shown today."""
	now = now or dt.datetime.now(dt.UTC)
	user = await session.get(User, user_id)
	timezone = user.timezone if user is not None else "UTC"
	zone = tz_or_utc(timezone)
	today = now.astimezone(zone).date()

	todos, events = await _todays_items(session, user_id, today=today, zone=zone)

	existing = await session.scalar(
		select(MorningView).where(
			MorningView.user_id == user_id, MorningView.local_date == today
		)
	)
	idea: ResurfacedIdea | None = None
	if existing is not None and existing.payload.get("resurfaced_idea"):
		idea = ResurfacedIdea.model_validate(existing.payload["resurfaced_idea"])
	else:
		picked = await pick_resurfacing(session, user_id, now=now, today=today)
		if picked is not None:
			idea = ResurfacedIdea(
				id=picked.id,
				capture_id=picked.capture_id,
				title=str(picked.payload.get("title", "")),
				summary=str(picked.payload.get("summary", "")),
				captured_on=picked.created_at.astimezone(zone).date(),
			)
			session.add(
				Resurfacing(
					id=uuid7(), user_id=user_id, derivation_id=picked.id, shown_on=today
				)
			)

	view = MorningViewOut(
		local_date=today,
		timezone=timezone,
		todos=todos,
		events=events,
		resurfaced_idea=idea,
		built_at=now,
	)
	payload: dict[str, Any] = view.model_dump(mode="json")
	await session.execute(
		insert(MorningView)
		.values(
			id=uuid7(), user_id=user_id, local_date=today, payload=payload, built_at=now
		)
		.on_conflict_do_update(
			constraint="uq_morning_views_user_date",
			set_={"payload": payload, "built_at": now},
		)
	)
	log.info(
		"morning.built",
		user_id=str(user_id),
		local_date=today.isoformat(),
		todos=len(todos),
		events=len(events),
		resurfaced=idea is not None,
	)
	return view


async def todays_view(
	session: AsyncSession, user_id: uuid.UUID, *, now: dt.datetime | None = None
) -> MorningViewOut | None:
	"""Today's pinned idea with live todos and events, or None before the first build.

	A pure read: nothing is written, so refetching on every foreground is cheap."""
	now = now or dt.datetime.now(dt.UTC)
	user = await session.get(User, user_id)
	timezone = user.timezone if user is not None else "UTC"
	zone = tz_or_utc(timezone)
	today = now.astimezone(zone).date()
	existing = await session.scalar(
		select(MorningView).where(
			MorningView.user_id == user_id, MorningView.local_date == today
		)
	)
	if existing is None:
		return None
	pinned = MorningViewOut.model_validate(existing.payload)
	idea = pinned.resurfaced_idea
	if idea is not None:
		# Pinned is not frozen: an idea rejected or deleted since 04:00 leaves the view.
		source = await session.get(Derivation, idea.id)
		if source is None or source.status == "rejected":
			idea = None
	todos, events = await _todays_items(session, user_id, today=today, zone=zone)
	return pinned.model_copy(
		update={
			"timezone": timezone,
			"todos": todos,
			"events": events,
			"resurfaced_idea": idea,
		}
	)
