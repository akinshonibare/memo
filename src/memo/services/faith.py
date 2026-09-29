"""Faith (ADR 0042): passages found in faith notes, prayers, the reading plan's
position, the one thing a day the Faith screen remembers, and the week's facts.

No score anywhere. The plan keeps time by position, so a week away is a week paused;
open prayers are never overdue; the weekly section lists what happened and counts
nothing against anyone.
"""

import datetime as dt
import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from memo.ids import uuid7
from memo.models import Capture, Derivation, FaithReading, Person, User
from memo.schemas.derivations import PASSAGE, PRAYER, UNREVIEWED
from memo.schemas.faith import (
	FaithWeek,
	MomentOut,
	PassageOut,
	PassageWritten,
	PlanOut,
	PrayerOut,
	ReadingOut,
)
from memo.services.morning import local_day_range, look_back_days, tz_or_utc
from memo.services.scripture import CANONICAL, PLAN_TITLES, Reading, find_passages, plan

# Stamped on passage rows. Found by code, not a model, so it is not an extractor
# version in the evals' sense; a change to the rules is still a new version.
SCRIPTURE_VERSION = "scripture-v1"
# How long a passage waits before it can come back: yesterday's reading isn't history.
PASSAGE_MIN_AGE = dt.timedelta(days=7)
EXCERPT_CHARS = 280
ANSWERED_SHOWN = 20
FAITH = "faith"


def faith_hidden(user: User | None) -> bool:
	"""Whether faith notes are kept out of Home, search, Ask and On this day. Only
	while Faith is on: turned off, they are ordinary notes again."""
	return bool(user is not None and user.faith_enabled)


# --- passages -------------------------------------------------------------------------


async def replace_passages(session: AsyncSession, capture: Capture) -> int:
	"""The passages a faith note is about, found again from its text. Idempotent by
	replacement, like extraction. A note that isn't in Faith has none."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == PASSAGE,
			Derivation.status.in_(UNREVIEWED),
		)
	)
	if capture.space != FAITH:
		return 0
	found = find_passages(capture.text or "")
	for passage in found:
		session.add(
			Derivation(
				id=uuid7(),
				user_id=capture.user_id,
				capture_id=capture.id,
				kind=PASSAGE,
				payload=passage.payload(),
				# Kept without a look, like auto-accept (ADR 0030): never a label.
				status="accepted",
				extractor_version=SCRIPTURE_VERSION,
				confidence=None,
			)
		)
	return len(found)


def passage_out(d: Derivation) -> PassageOut:
	return PassageOut.model_validate(d.payload)


# --- prayers --------------------------------------------------------------------------


async def names_for(
	session: AsyncSession, user_id: uuid.UUID, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, str]:
	wanted = {i for i in ids if i is not None}
	if not wanted:
		return {}
	rows = await session.execute(
		select(Person.id, Person.name).where(
			Person.user_id == user_id, Person.id.in_(wanted)
		)
	)
	return {pid: name for pid, name in rows.all()}


def prayer_out(d: Derivation, names: dict[uuid.UUID, str]) -> PrayerOut:
	return PrayerOut(
		id=d.id,
		title=str(d.payload.get("title", "")),
		notes=d.payload.get("notes"),
		answer=d.payload.get("answer"),
		person_id=d.person_id,
		person_name=names.get(d.person_id) if d.person_id else None,
		capture_id=d.capture_id,
		created_at=d.created_at,
		answered_at=d.completed_at,
	)


async def prayers_out(
	session: AsyncSession, user_id: uuid.UUID, rows: Sequence[Derivation]
) -> list[PrayerOut]:
	names = await names_for(session, user_id, (d.person_id for d in rows))
	return [prayer_out(d, names) for d in rows]


def live_prayers(user_id: uuid.UUID):  # type: ignore[no-untyped-def]
	return select(Derivation).where(
		Derivation.user_id == user_id,
		Derivation.kind == PRAYER,
		Derivation.status != "rejected",
	)


# --- the reading plan -----------------------------------------------------------------


def reading_out(r: Reading) -> ReadingOut:
	return ReadingOut(
		index=r.index,
		label=r.label,
		book=r.usfm,
		chapter=r.chapter,
		end_chapter=r.end_chapter,
	)


async def plan_state(
	session: AsyncSession, user_id: uuid.UUID, name: str = CANONICAL
) -> PlanOut:
	"""Where the user is. The latest time through that isn't finished; once one is,
	the next begins at Genesis without anyone pressing anything."""
	readings = plan(name)
	row = (
		await session.execute(
			select(FaithReading.cycle, FaithReading.position, FaithReading.read_at)
			.where(FaithReading.user_id == user_id, FaithReading.plan == name)
			.order_by(FaithReading.cycle.desc(), FaithReading.position.desc())
			.limit(1)
		)
	).first()
	if row is None:
		cycle, position, last, last_read_at = 1, 0, None, None
	else:
		cycle, position = row.cycle, row.position + 1
		last, last_read_at = readings[row.position], row.read_at
		if position >= len(readings):
			cycle, position = cycle + 1, 0
	return PlanOut(
		plan=name,
		title=PLAN_TITLES[name],
		cycle=cycle,
		position=position,
		total=len(readings),
		next=reading_out(readings[position]),
		last=reading_out(last) if last is not None else None,
		last_read_at=last_read_at,
	)


async def mark_read(
	session: AsyncSession, user_id: uuid.UUID, name: str = CANONICAL
) -> PlanOut:
	"""The next reading, marked read. A double tap is one reading: the insert names the
	position it expects, and a second one for the same position does nothing."""
	state = await plan_state(session, user_id, name)
	await session.execute(
		insert(FaithReading)
		.values(
			id=uuid7(),
			user_id=user_id,
			plan=name,
			cycle=state.cycle,
			position=state.position,
		)
		.on_conflict_do_nothing(constraint="uq_faith_readings_position")
	)
	await session.flush()
	return await plan_state(session, user_id, name)


async def undo_read(
	session: AsyncSession, user_id: uuid.UUID, name: str = CANONICAL
) -> PlanOut:
	"""Take back the last reading marked read. Nothing marked: nothing to undo."""
	latest = await session.scalar(
		select(FaithReading)
		.where(FaithReading.user_id == user_id, FaithReading.plan == name)
		.order_by(FaithReading.cycle.desc(), FaithReading.position.desc())
		.limit(1)
	)
	if latest is not None:
		await session.delete(latest)
		await session.flush()
	return await plan_state(session, user_id, name)


# --- the one thing a day --------------------------------------------------------------


async def moment(
	session: AsyncSession, user: User, *, now: dt.datetime | None = None
) -> MomentOut | None:
	"""At most one thing, the same all day. First, a prayer prayed on this date in
	another year (a year back, six months, a month: On this day's days, ADR 0040),
	furthest back first, answered before unanswered. Otherwise a passage the user
	wrote about at least a week ago, a different one each day. Otherwise nothing."""
	zone = tz_or_utc(user.timezone)
	now = now or dt.datetime.now(dt.UTC)
	today = now.astimezone(zone).date()
	days = look_back_days(today)
	ranges = [local_day_range(d, zone) for d in days]
	prayed = (
		await session.scalars(
			live_prayers(user.id).where(
				or_(
					*(
						and_(Derivation.created_at >= s, Derivation.created_at < e)
						for s, e in ranges
					)
				)
			)
		)
	).all()
	if prayed:
		rank = {d: i for i, d in enumerate(days)}
		best = min(
			prayed,
			key=lambda d: (
				rank.get(d.created_at.astimezone(zone).date(), len(days)),
				d.completed_at is None,
				d.created_at,
			),
		)
		names = await names_for(session, user.id, [best.person_id])
		return MomentOut(
			kind="prayer",
			prayer=prayer_out(best, names),
			on=best.created_at.astimezone(zone).date(),
		)

	cutoff = local_day_range(today, zone)[0] - PASSAGE_MIN_AGE
	rows = (
		await session.execute(
			select(Derivation, Capture.text, Capture.created_at)
			.join(
				Capture,
				and_(
					Capture.id == Derivation.capture_id,
					Capture.user_id == Derivation.user_id,
				),
			)
			.where(
				Derivation.user_id == user.id,
				Derivation.kind == PASSAGE,
				Derivation.status != "rejected",
				Capture.space == FAITH,
				Capture.created_at < cutoff,
			)
			.order_by(Capture.created_at, Derivation.id)
		)
	).all()
	seen: set[str] = set()
	candidates = []
	for d, body, written in rows:
		ref = str(d.payload.get("ref"))
		if ref in seen:
			continue
		seen.add(ref)
		candidates.append((d, body, written))
	if not candidates:
		return None
	d, body, written = candidates[today.toordinal() % len(candidates)]
	return MomentOut(
		kind="passage",
		passage=passage_out(d),
		on=written.astimezone(zone).date(),
		capture_id=d.capture_id,
		excerpt=(body or "").strip()[:EXCERPT_CHARS] or None,
	)


# --- the week -------------------------------------------------------------------------


async def week(
	session: AsyncSession, user_id: uuid.UUID, *, since: dt.datetime, until: dt.datetime
) -> FaithWeek:
	"""What was read, which prayers were answered, and which passages were written
	about, in [since, until). Facts, nothing to beat."""
	reads = (
		await session.scalars(
			select(FaithReading)
			.where(
				FaithReading.user_id == user_id,
				FaithReading.read_at >= since,
				FaithReading.read_at < until,
			)
			.order_by(FaithReading.read_at, FaithReading.position)
		)
	).all()
	read = []
	for r in reads:
		readings = plan(r.plan)
		if 0 <= r.position < len(readings):
			read.append(reading_out(readings[r.position]))

	answered_rows = (
		await session.scalars(
			live_prayers(user_id)
			.where(Derivation.completed_at >= since, Derivation.completed_at < until)
			.order_by(Derivation.completed_at)
		)
	).all()

	passage_rows = (
		await session.scalars(
			select(Derivation)
			.join(
				Capture,
				and_(
					Capture.id == Derivation.capture_id,
					Capture.user_id == Derivation.user_id,
				),
			)
			.where(
				Derivation.user_id == user_id,
				Derivation.kind == PASSAGE,
				Derivation.status != "rejected",
				Capture.space == FAITH,
				Capture.created_at >= since,
				Capture.created_at < until,
			)
			.order_by(Capture.created_at, Derivation.id)
		)
	).all()
	passages: list[PassageWritten] = []
	seen: set[str] = set()
	for d in passage_rows:
		ref = str(d.payload.get("ref"))
		if ref not in seen:
			seen.add(ref)
			passages.append(
				PassageWritten(passage=passage_out(d), capture_id=d.capture_id)
			)

	return FaithWeek(
		read=read,
		answered=await prayers_out(session, user_id, answered_rows),
		passages=passages,
	)


async def readings_count(session: AsyncSession, user_id: uuid.UUID) -> int:
	return int(
		await session.scalar(
			select(func.count())
			.select_from(FaithReading)
			.where(FaithReading.user_id == user_id)
		)
		or 0
	)
