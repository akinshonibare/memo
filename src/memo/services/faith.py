"""Faith (ADR 0042): passages and prayers found in notes, the reading plan's position,
the one thing a day the Faith screen remembers, and the week's facts.

No score anywhere. The plan keeps time by position, so a week away is a week paused;
open prayers are never overdue; the weekly section lists what happened and counts
nothing against anyone.
"""

import datetime as dt
import re
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.ids import uuid7
from memo.models import Capture, Derivation, FaithReading, Person, User
from memo.prompts.faith_v1 import (
	FAITH_VERSION,
	SYSTEM_PROMPT,
	repair_message,
	user_message,
)
from memo.schemas.derivations import MENTION, PASSAGE, PRAYER, UNREVIEWED
from memo.schemas.faith import (
	FaithExtraction,
	FaithWeek,
	MomentOut,
	PassageOut,
	PassageWritten,
	PlanOut,
	PrayerOut,
	ReadingOut,
)
from memo.services.extraction import AnthropicExtractionProvider, complete_validated
from memo.services.llm import LLMCall
from memo.services.morning import local_day_range, look_back_days, tz_or_utc
from memo.services.people import normalize
from memo.services.retry import TerminalError
from memo.services.scripture import CANONICAL, PLAN_TITLES, Reading, find_passages, plan

log = structlog.get_logger("memo.faith")

# Stamped on passage rows. Found by code, not a model, so it is not an extractor
# version in the evals' sense; a change to the rules is still a new version.
SCRIPTURE_VERSION = "scripture-v1"
# How long a passage waits before it can come back: yesterday's reading isn't history.
PASSAGE_MIN_AGE = dt.timedelta(days=7)
EXCERPT_CHARS = 280
ANSWERED_SHOWN = 20
FAITH = "faith"


def faith_on(user: User | None) -> bool:
	return bool(user is not None and user.faith_enabled)


def faith_hidden(user: User | None) -> bool:
	"""Whether faith notes are kept out of Home, search, Ask and On this day. Only
	while Faith is on: turned off, they are ordinary notes again."""
	return bool(user is not None and user.faith_enabled)


# --- passages -------------------------------------------------------------------------


async def replace_passages(
	session: AsyncSession, capture: Capture, *, enabled: bool = False
) -> int:
	"""The passages a note is about, found again from its text. Idempotent by
	replacement, like extraction. A faith note always has them; any other note only
	while Faith is on (`enabled`), since that is when memo files them into Faith."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == PASSAGE,
			Derivation.status.in_(UNREVIEWED),
		)
	)
	if capture.space != FAITH and not enabled:
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


# --- prayers found in notes ----------------------------------------------------------

# Only a note with one of these words is read for prayers: most notes have none, and
# this keeps the model call to the notes that might. A prayer that names none of them
# ("asking for wisdom about the move") is missed; the eval's recall bar watches for it.
_PRAYERISH = re.compile(
	r"\b(pray\w*|god|lord|jesus|christ|holy spirit|father in heaven|amen|bless\w*)\b",
	re.IGNORECASE,
)
# A todo the extractor made from the same words ("Pray for Tunde's mum"): with Faith on
# it is the prayer, so it goes rather than sit in the Planner and Reminders twice.
_PRAYER_TODO = re.compile(r"^\s*(keep\s+)?pray(ing)?\b", re.IGNORECASE)


def might_pray(text: str | None) -> bool:
	return bool(text and _PRAYERISH.search(text))


@dataclass(frozen=True)
class FaithResult:
	extraction: FaithExtraction
	raw: str
	calls: list[LLMCall]


class FaithReaderLike(Protocol):
	@property
	def model(self) -> str: ...

	@property
	def version(self) -> str: ...

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> FaithResult: ...


class FaithReader:
	def __init__(self, provider: AnthropicExtractionProvider, *, max_repairs: int = 1):
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{FAITH_VERSION}/{self.model}"

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> FaithResult:
		extraction, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=user_message(text, captured_at=captured_at, timezone=timezone),
			parse=FaithExtraction.model_validate_json,
			repair=repair_message,
			stage="faith",
			max_repairs=self._max_repairs,
		)
		return FaithResult(extraction=extraction, raw=raw, calls=calls)


class UnconfiguredFaithReader:
	"""Stands in when no Anthropic key is set, so the stage fails visibly."""

	model = "unconfigured"
	version = f"{FAITH_VERSION}/unconfigured"

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> FaithResult:
		raise TerminalError("the faith stage is not configured (no ANTHROPIC_API_KEY)")


def make_faith_reader(settings: Settings) -> FaithReaderLike:
	if settings.anthropic_api_key is None:
		return UnconfiguredFaithReader()
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return FaithReader(
		AnthropicExtractionProvider(
			client, model=settings.extraction_model, schema=FaithExtraction
		)
	)


async def _people_in_note(
	session: AsyncSession, capture: Capture
) -> dict[str, uuid.UUID]:
	"""The names the people stage linked in this note, normalised, to whom. A prayer is
	linked only by an exact match here: "Tunde's mum" never becomes Tunde."""
	rows = (
		await session.execute(
			select(
				Derivation.payload, Derivation.person_id, Person.name, Person.aliases
			)
			.join(
				Person,
				and_(
					Person.id == Derivation.person_id,
					Person.user_id == Derivation.user_id,
				),
			)
			.where(
				Derivation.capture_id == capture.id,
				Derivation.user_id == capture.user_id,
				Derivation.kind == MENTION,
				Derivation.status != "rejected",
			)
		)
	).all()
	names: dict[str, uuid.UUID] = {}
	for payload, person_id, name, aliases in rows:
		for said in (payload.get("name"), payload.get("full_name"), name, *aliases):
			if said:
				names.setdefault(normalize(str(said)), person_id)
	return names


async def apply_prayers(
	session: AsyncSession,
	capture: Capture,
	extraction: FaithExtraction,
	*,
	version: str,
	extract_version: str,
) -> list[Derivation]:
	"""File the prayers found in a note into Faith. Idempotent by replacement: a re-run
	of this version drops only prayers nobody has touched. A "Pray for..." todo from the
	same note, untouched, goes: with Faith on, it is the prayer."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == PRAYER,
			Derivation.extractor_version == version,
			Derivation.status.in_(UNREVIEWED),
		)
	)
	people = await _people_in_note(session, capture) if extraction.prayers else {}
	made = []
	for found in extraction.prayers:
		person_id = (
			people.get(normalize(found.for_person)) if found.for_person else None
		)
		prayer = Derivation(
			id=uuid7(),
			user_id=capture.user_id,
			capture_id=capture.id,
			kind=PRAYER,
			payload={"title": found.title, "notes": None, "answer": None},
			# Shown in Faith at once, like a passage; removing one is the "wrong" label.
			status="accepted",
			extractor_version=version,
			confidence=found.confidence,
			person_id=person_id,
		)
		session.add(prayer)
		made.append(prayer)
	if made:
		todos = (
			await session.scalars(
				select(Derivation).where(
					Derivation.capture_id == capture.id,
					Derivation.user_id == capture.user_id,
					Derivation.kind == "todo",
					Derivation.extractor_version == extract_version,
					Derivation.status.in_(UNREVIEWED),
				)
			)
		).all()
		for todo in todos:
			if _PRAYER_TODO.match(str(todo.payload.get("title", ""))):
				await session.delete(todo)
	return made


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
