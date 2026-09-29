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
from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from memo.ids import uuid7
from memo.models import Capture, Derivation, MorningView, Resurfacing, User
from memo.schemas.habits import HabitPrompt, TodayMilestone
from memo.schemas.people import ReachOut
from memo.services.milestones import todays_habits
from memo.services.people import reach_out

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


class OnThisDay(BaseModel):
	capture_id: uuid.UUID
	kind: str
	text: str
	captured_on: dt.date


class MorningViewOut(BaseModel):
	local_date: dt.date
	timezone: str
	todos: list[TodoItem]
	events: list[EventItem]
	resurfaced_idea: ResurfacedIdea | None
	built_at: dt.datetime
	# People it may be time to get in touch with (ADR 0029). Read live, like todos,
	# so "Spoke to them" takes someone off today's list at once. Defaulted so a view
	# stored before this field existed still reads.
	reach_out: list[ReachOut] = []
	# A note from this day in another year, or six months or a month ago (ADR 0040).
	# Read live like the items, and defaulted for views stored before it existed.
	on_this_day: OnThisDay | None = None
	# Today's one habit line (ADR 0043): a suggestion, or a nudge. Live, and only while
	# Habits is on.
	habit: HabitPrompt | None = None
	# A milestone a tracked habit reached in the last couple of days (ADR 0050).
	milestone: TodayMilestone | None = None


LOOK_BACK_YEARS = 10
ON_THIS_DAY_CHARS = 600


def _months_back(day: dt.date, months: int) -> dt.date | None:
	"""The same day of the month `months` earlier, or None where it doesn't exist
	(no 31 September, no 29 February in most years): that day simply has no match."""
	month = day.month - months
	year = day.year + (month - 1) // 12
	month = (month - 1) % 12 + 1
	try:
		return day.replace(year=year, month=month)
	except ValueError:
		return None


def look_back_days(today: dt.date) -> list[dt.date]:
	"""The days "on this day" looks at, best first: whole years back, the furthest
	first (the longer ago, the more it's worth seeing again), then six months, then
	one month."""
	years = [_months_back(today, 12 * n) for n in range(LOOK_BACK_YEARS, 0, -1)]
	recent = [_months_back(today, 6), _months_back(today, 1)]
	return [d for d in [*years, *recent] if d is not None]


def local_day_range(day: dt.date, zone: ZoneInfo) -> tuple[dt.datetime, dt.datetime]:
	"""The instants a local day spans, as [start, end). Local midnight to local
	midnight, so a day that gains or loses a DST hour is 25 or 23 hours long."""
	start = dt.datetime.combine(day, dt.time(), tzinfo=zone)
	end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time(), tzinfo=zone)
	return start.astimezone(dt.UTC), end.astimezone(dt.UTC)


async def on_this_day(
	session: AsyncSession,
	user_id: uuid.UUID,
	*,
	today: dt.date,
	zone: ZoneInfo,
	include_faith: bool = True,
) -> OnThisDay | None:
	"""One note from a day worth looking back on - never a feed. Rationed like the
	resurfaced idea, and for the same reason: one is a memory, five are a chore. Only
	notes that finished processing and have words; on a day with several, the longest.
	A pure read, so it's the same all day without being pinned.

	Each local day is turned into a UTC range here, not by converting every row's
	timestamp in SQL: plain ranges on `created_at` can use ix_captures_user_created.

	`include_faith=False` leaves faith notes out; they have their own place (ADR
	0042)."""
	days = look_back_days(today)
	ranges = [local_day_range(d, zone) for d in days]
	stmt = select(Capture.id, Capture.kind, Capture.text, Capture.created_at).where(
		Capture.user_id == user_id,
		Capture.status == "ready",
		func.length(func.trim(Capture.text)) > 0,
		or_(
			*(and_(Capture.created_at >= s, Capture.created_at < e) for s, e in ranges)
		),
	)
	if not include_faith:
		stmt = stmt.where(Capture.space == "main")
	rows = (await session.execute(stmt)).all()
	if not rows:
		return None
	rank = {d: i for i, d in enumerate(days)}
	best = min(
		rows, key=lambda r: (rank[r[3].astimezone(zone).date()], -len(r[2] or ""))
	)
	return OnThisDay(
		capture_id=best[0],
		kind=best[1],
		text=(best[2] or "").strip()[:ON_THIS_DAY_CHARS],
		captured_on=best[3].astimezone(zone).date(),
	)


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
		AND d.status IN ('confirmed', 'pending_review', 'user_created', 'accepted')
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
	# Done todos leave the list; nothing done is shown or counted (no guilt, no score).
	live = select(Derivation).where(
		Derivation.user_id == user_id,
		Derivation.status != "rejected",
		Derivation.completed_at.is_(None),
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
	people = await reach_out(session, user_id, today=today, zone=zone)
	looked_back = await on_this_day(
		session,
		user_id,
		today=today,
		zone=zone,
		include_faith=not (user is not None and user.faith_enabled),
	)

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

	habit, milestone = (
		await todays_habits(session, user, now=now) if user else (None, None)
	)
	view = MorningViewOut(
		local_date=today,
		timezone=timezone,
		todos=todos,
		events=events,
		resurfaced_idea=idea,
		built_at=now,
		reach_out=people,
		on_this_day=looked_back,
		habit=habit,
		milestone=milestone,
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
		on_this_day=looked_back is not None,
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
	people = await reach_out(session, user_id, today=today, zone=zone)
	looked_back = await on_this_day(
		session,
		user_id,
		today=today,
		zone=zone,
		include_faith=not (user is not None and user.faith_enabled),
	)
	habit, milestone = (
		await todays_habits(session, user, now=now) if user else (None, None)
	)
	return pinned.model_copy(
		update={
			"timezone": timezone,
			"todos": todos,
			"events": events,
			"resurfaced_idea": idea,
			"reach_out": people,
			"on_this_day": looked_back,
			"habit": habit,
			"milestone": milestone,
		}
	)
