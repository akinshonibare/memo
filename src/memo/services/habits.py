"""Habits (ADR 0043): the activities a note says you did, the habits you track, and
the days each one counts.

Counts are computed on every read, never stored: from activities (found in notes or
logged with a tap), from People's contacts with family, and from the passages Faith
finds in notes. A stored count would have to follow every rejection, edit and merge.

Rhythm, never a streak: a habit shows this week's days against an optional target, the
last time, and eight weeks of bars. Nothing resets and nothing is overdue.
"""

import datetime as dt
import math
import re
import uuid
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol
from zoneinfo import ZoneInfo

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import and_, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.ids import uuid7
from memo.models import Capture, Derivation, Habit, Person, User
from memo.prompts.habits_v2 import (
	HABITS_VERSION,
	KEYS,
	SYSTEM_PROMPT,
	repair_message,
	user_message,
)
from memo.schemas.derivations import ACTIVITY, MENTION, PASSAGE, READING, UNREVIEWED
from memo.schemas.habits import (
	UNITS,
	DayOut,
	Evidence,
	HabitDetail,
	HabitOut,
	HabitPrompt,
	HabitsExtraction,
	HabitWeek,
	Suggestion,
	activity_payload,
	normalize_key,
)
from memo.schemas.people import IN_TOUCH
from memo.services.extraction import AnthropicExtractionProvider, complete_validated
from memo.services.llm import LLMCall
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.habits")

FAMILY = "family"
BIBLE = "bible"
HEALTH = "health"  # an activity's payload source when it came from Apple Health
SUGGEST_WINDOW_DAYS = 21
SUGGEST_MIN_DAYS = 3
HISTORY_WEEKS = 8
DETAIL_DAYS = 60
EXCERPT_CHARS = 160

NAMES: dict[str, str] = {
	"gym": "Gym",
	"running": "Running",
	"walking": "Walking",
	"cycling": "Cycling",
	"swimming": "Swimming",
	"yoga": "Yoga",
	"sport": "Sport",
	"reading": "Reading",
	BIBLE: "Reading the Bible",
	"prayer": "Prayer",
	"journalling": "Journalling",
	"meditation": "Meditation",
	"cooking": "Cooking",
	"music": "Music practice",
	"study": "Study",
	"early night": "Early nights",
	FAMILY: "Time with family",
}

# What someone typing a habit's name most likely means, so "exercise" counts the gym
# notes memo already files, rather than a new key nothing is ever filed under.
SYNONYMS: dict[str, str] = {
	"exercise": "gym",
	"workout": "gym",
	"workouts": "gym",
	"weights": "gym",
	"lifting": "gym",
	"run": "running",
	"runs": "running",
	"jogging": "running",
	"walk": "walking",
	"walks": "walking",
	"read": "reading",
	"books": "reading",
	"reading books": "reading",
	"bible reading": BIBLE,
	"read the bible": BIBLE,
	"reading the bible": BIBLE,
	"scripture": BIBLE,
	"pray": "prayer",
	"praying": "prayer",
	"journal": "journalling",
	"journaling": "journalling",
	"meditate": "meditation",
	"cook": "cooking",
	"piano": "music",
	"guitar": "music",
	"practice": "music",
	"learning": "study",
	"sleep": "early night",
	"early nights": "early night",
	"bed early": "early night",
	"family": FAMILY,
	"time with family": FAMILY,
	"call family": FAMILY,
	"talk to family": FAMILY,
	"talking to family": FAMILY,
}


def name_for(key: str) -> str:
	return NAMES.get(key) or key[:1].upper() + key[1:]


def key_for(name: str) -> str:
	key = normalize_key(name)
	return SYNONYMS.get(key, key)


def source_for(key: str) -> str:
	return FAMILY if key == FAMILY else BIBLE if key == BIBLE else "activity"


def tz_or_utc(name: str) -> ZoneInfo:
	# Its own copy of services/morning's: morning shows today's habit line, so it
	# imports this module, and this one must not import it back.
	try:
		return ZoneInfo(name)
	except (KeyError, ValueError):
		return ZoneInfo("UTC")


def habits_on(user: User | None) -> bool:
	return bool(user is not None and user.habits_enabled)


# --- the reader -----------------------------------------------------------------------


@dataclass(frozen=True)
class HabitsResult:
	extraction: HabitsExtraction
	raw: str
	calls: list[LLMCall]


class HabitsReaderLike(Protocol):
	@property
	def model(self) -> str: ...

	@property
	def version(self) -> str: ...

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		extra_keys: list[str] | None = None,
	) -> HabitsResult: ...


def _local_date(moment: dt.datetime, timezone: str) -> dt.date:
	try:
		return moment.astimezone(ZoneInfo(timezone)).date()
	except (KeyError, ValueError):
		return moment.astimezone(dt.UTC).date()


class HabitsReader:
	def __init__(self, provider: AnthropicExtractionProvider, *, max_repairs: int = 1):
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{HABITS_VERSION}/{self.model}"

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		extra_keys: list[str] | None = None,
	) -> HabitsResult:
		captured_on = _local_date(captured_at, timezone)

		def parse(raw: str) -> HabitsExtraction:
			return HabitsExtraction.model_validate_json(
				raw, context={"captured_on": captured_on}
			)

		first = user_message(text, captured_at=captured_at, timezone=timezone)
		if extra_keys:
			# The author's own habits: data for this read, not a change to the prompt.
			first += (
				"\n\nThe author also tracks these, so use exactly these keys when they "
				f"fit: {', '.join(extra_keys)}."
			)
		extraction, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=first,
			parse=parse,
			repair=repair_message,
			stage="habits",
			max_repairs=self._max_repairs,
		)
		return HabitsResult(extraction=extraction, raw=raw, calls=calls)


class UnconfiguredHabitsReader:
	"""Stands in when no Anthropic key is set, so the stage fails visibly."""

	model = "unconfigured"
	version = f"{HABITS_VERSION}/unconfigured"

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		extra_keys: list[str] | None = None,
	) -> HabitsResult:
		raise TerminalError("the habits stage is not configured (no ANTHROPIC_API_KEY)")


def make_habits_reader(settings: Settings) -> HabitsReaderLike:
	if settings.anthropic_api_key is None:
		return UnconfiguredHabitsReader()
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return HabitsReader(
		AnthropicExtractionProvider(
			client, model=settings.extraction_model, schema=HabitsExtraction
		)
	)


async def own_keys(session: AsyncSession, user_id: uuid.UUID) -> list[str]:
	"""Keys of habits the author made up, which the reader's vocabulary doesn't have."""
	rows = await session.scalars(
		select(Habit.key).where(
			Habit.user_id == user_id,
			Habit.status == "active",
			Habit.source == "activity",
		)
	)
	return sorted(k for k in rows.all() if k not in KEYS)


async def apply_activities(
	session: AsyncSession,
	capture: Capture,
	extraction: HabitsExtraction,
	*,
	version: str,
) -> list[Derivation]:
	"""File what a note says you did. Idempotent by replacement: a re-run of this
	version drops only activities nobody removed (a removal is a rejection, kept)."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == ACTIVITY,
			Derivation.extractor_version == version,
			Derivation.status.in_(UNREVIEWED),
		)
	)
	# A day you removed stays removed: its rejected rows are kept, and a re-read of the
	# same note must not file it again.
	removed: list[dict[str, Any]] = list(
		await session.scalars(
			select(Derivation.payload).where(
				Derivation.capture_id == capture.id,
				Derivation.user_id == capture.user_id,
				Derivation.kind == ACTIVITY,
				Derivation.status == "rejected",
			)
		)
	)
	seen: set[tuple[str, dt.date]] = set()
	for payload in removed:
		try:
			seen.add(
				(str(payload["key"]), dt.date.fromisoformat(payload["occurred_on"]))
			)
		except (KeyError, ValueError):
			continue
	made: list[Derivation] = []
	for found in extraction.activities:
		if (found.key, found.occurred_on) in seen:
			continue
		seen.add((found.key, found.occurred_on))
		activity = Derivation(
			id=uuid7(),
			user_id=capture.user_id,
			capture_id=capture.id,
			kind=ACTIVITY,
			payload=activity_payload(found),
			status="accepted",
			extractor_version=version,
			confidence=found.confidence,
		)
		session.add(activity)
		made.append(activity)
	return made


# --- who is family --------------------------------------------------------------------

_FAMILY_WORDS = re.compile(
	r"\b(mum|mom|mummy|mommy|mother|mama|dad|daddy|father|papa|brother|bro|sister|sis|"
	r"sibling|son|daughter|wife|husband|grandma|grandpa|granny|grandad|grandmother|"
	r"grandfather|nan|nana|aunt|auntie|aunty|uncle|cousin|niece|nephew|in-law|"
	r"stepmum|stepdad)\b",
	re.IGNORECASE,
)


def looks_like_family(person: Person) -> bool:
	"""A guess from a relationship word in the name, an alias or the note. Never from
	a first name alone: "Tunde" is not guessed to be anyone's brother."""
	said = " ".join([person.name, person.note or "", *(person.aliases or [])])
	return bool(_FAMILY_WORDS.search(said))


def is_family(person: Person) -> bool:
	return person.family if person.family is not None else looks_like_family(person)


# --- the days a habit counts ----------------------------------------------------------


@dataclass(frozen=True)
class Effort:
	"""One number an activity came with (ADR 0050), in km, minutes or kg."""

	day: dt.date
	measure: str
	value: float
	lift: str
	capture_id: uuid.UUID | None


@dataclass
class Days:
	"""Each day that counts, with the evidence for it, and the numbers given."""

	by_day: dict[dt.date, list[Evidence]] = field(
		default_factory=lambda: defaultdict(list)
	)
	efforts: list[Effort] = field(default_factory=list)

	def add(self, day: dt.date, evidence: Evidence) -> None:
		self.by_day[day].append(evidence)

	def merge(self, other: "Days") -> "Days":
		out = Days()
		for source in (self, other):
			for day, items in source.by_day.items():
				out.by_day[day].extend(items)
			out.efforts.extend(source.efforts)
		return out

	@property
	def dates(self) -> set[dt.date]:
		return set(self.by_day)


async def activity_days(
	session: AsyncSession, user_id: uuid.UUID, keys: Iterable[str] | None = None
) -> dict[str, Days]:
	"""Every activity that still counts, by key."""
	stmt = (
		select(Derivation, Capture.text)
		.outerjoin(
			Capture,
			and_(
				Capture.id == Derivation.capture_id,
				Capture.user_id == Derivation.user_id,
			),
		)
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == ACTIVITY,
			Derivation.status != "rejected",
		)
	)
	wanted = set(keys) if keys is not None else None
	if wanted is not None:
		stmt = stmt.where(Derivation.payload["key"].astext.in_(sorted(wanted)))
	out: dict[str, Days] = defaultdict(Days)
	for d, body in (await session.execute(stmt)).all():
		try:
			day = dt.date.fromisoformat(str(d.payload.get("occurred_on")))
		except ValueError:
			continue
		key = str(d.payload.get("key"))
		if d.payload.get("source") == HEALTH:
			evidence = Evidence(
				kind="health", text=health_text(d.payload), activity_id=d.id
			)
		elif d.capture_id is None:
			evidence = Evidence(kind="logged", activity_id=d.id)
		else:
			excerpt = (body or "").strip()[:EXCERPT_CHARS] or None
			evidence = Evidence(
				kind="note",
				text=excerpt or str(d.payload.get("label") or ""),
				capture_id=d.capture_id,
				activity_id=d.id,
			)
		out[key].add(day, evidence)
		out[key].efforts.extend(_efforts(d.payload, day, d.capture_id))
	return out


def _efforts(
	payload: dict[str, Any], day: dt.date, capture_id: uuid.UUID | None
) -> list[Effort]:
	out = []
	for amount in payload.get("amounts") or []:
		try:
			value = float(amount["value"])
			measure = str(amount["measure"])
		except (KeyError, TypeError, ValueError):
			continue
		if value > 0 and measure in UNITS:
			out.append(
				Effort(day, measure, value, str(amount.get("lift") or ""), capture_id)
			)
	return out


def _amount_text(measure: str, value: float) -> str:
	if measure == "distance":
		return f"{value:.1f} km" if value < 100 else f"{value:.0f} km"
	if measure == "duration":
		minutes = round(value)
		return (
			f"{minutes} min"
			if minutes < 60
			else f"{minutes // 60} h {minutes % 60} min"
		)
	return f"{value:g} kg"


def health_text(payload: dict[str, Any]) -> str:
	"""A workout from Apple Health as its day's evidence: "Run · 5.2 km · 28 min"."""
	parts = [str(payload.get("label") or "Workout")]
	for amount in payload.get("amounts") or []:
		try:
			parts.append(_amount_text(str(amount["measure"]), float(amount["value"])))
		except (KeyError, TypeError, ValueError):
			continue
	return " · ".join(parts)


_CONTACT_WORDS = {
	"in_person": "Saw",
	"call": "Called",
	"message": "Texted",
	"unspecified": "In touch with",
}


async def family_days(session: AsyncSession, user_id: uuid.UUID) -> Days:
	"""The days People recorded real contact with someone who is family."""
	people = (
		await session.scalars(select(Person).where(Person.user_id == user_id))
	).all()
	family = {p.id: p.name for p in people if is_family(p)}
	days = Days()
	if not family:
		return days
	rows = (
		await session.scalars(
			select(Derivation).where(
				Derivation.user_id == user_id,
				Derivation.kind == MENTION,
				Derivation.status != "rejected",
				Derivation.person_id.in_(family.keys()),
				Derivation.payload["contact"].astext.in_(sorted(IN_TOUCH)),
			)
		)
	).all()
	for m in rows:
		try:
			day = dt.date.fromisoformat(str(m.payload.get("occurred_on")))
		except ValueError:
			continue
		assert m.person_id is not None
		verb = _CONTACT_WORDS.get(str(m.payload.get("contact")), "In touch with")
		days.add(
			day,
			Evidence(
				kind="contact",
				text=f"{verb} {family[m.person_id]}",
				capture_id=m.capture_id,
			),
		)
	return days


async def bible_days(session: AsyncSession, user_id: uuid.UUID, zone: ZoneInfo) -> Days:
	"""Faith's side of reading the Bible (ADR 0042): the days you logged a reading,
	and the days a note named a passage."""
	days = Days()
	for r in (
		await session.scalars(
			select(Derivation).where(
				Derivation.user_id == user_id, Derivation.kind == READING
			)
		)
	).all():
		try:
			day = dt.date.fromisoformat(str(r.payload.get("read_on")))
		except ValueError:
			continue
		days.add(day, Evidence(kind="reading", text=str(r.payload.get("ref"))))
	rows = (
		await session.execute(
			select(Derivation.payload, Capture.id, Capture.created_at)
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
			)
		)
	).all()
	for payload, capture_id, created_at in rows:
		days.add(
			created_at.astimezone(zone).date(),
			Evidence(
				kind="passage", text=str(payload.get("ref")), capture_id=capture_id
			),
		)
	return days


@dataclass
class Sources:
	"""Everything a user's habits count from, read once per request."""

	activities: dict[str, Days]
	family: Days
	bible: Days

	def days_for(self, key: str) -> Days:
		own = self.activities.get(key, Days())
		if key == FAMILY:
			return own.merge(self.family)
		if key == BIBLE:
			return own.merge(self.bible)
		return own


async def load_sources(session: AsyncSession, user: User) -> Sources:
	zone = tz_or_utc(user.timezone)
	return Sources(
		activities=await activity_days(session, user.id),
		family=await family_days(session, user.id),
		bible=await bible_days(session, user.id, zone),
	)


def today_for(user: User, now: dt.datetime | None = None) -> dt.date:
	return (now or dt.datetime.now(dt.UTC)).astimezone(tz_or_utc(user.timezone)).date()


def habit_out(habit: Habit, days: Days, today: dt.date) -> HabitOut:
	dates = {d for d in days.dates if d <= today}
	monday = today - dt.timedelta(days=today.weekday())
	weeks = []
	for back in range(HISTORY_WEEKS - 1, -1, -1):
		start = monday - dt.timedelta(weeks=back)
		weeks.append(sum(1 for d in dates if start <= d < start + dt.timedelta(days=7)))
	return HabitOut(
		id=habit.id,
		key=habit.key,
		name=habit.name,
		source=habit.source,
		target_per_week=habit.target_per_week,
		this_week=weeks[-1],
		week_days=[monday + dt.timedelta(days=i) in dates for i in range(7)],
		last_done=max(dates) if dates else None,
		total_days=len(dates),
		since=min(dates) if dates else None,
		weeks=weeks,
	)


def habit_detail(habit: Habit, days: Days, today: dt.date) -> HabitDetail:
	base = habit_out(habit, days, today)
	floor = today - dt.timedelta(days=DETAIL_DAYS)
	out = []
	for day in sorted((d for d in days.dates if floor < d <= today), reverse=True):
		evidence = days.by_day[day]
		out.append(
			DayOut(
				date=day,
				evidence=evidence,
				removable=any(e.activity_id is not None for e in evidence),
			)
		)
	return HabitDetail(**base.model_dump(), days=out)


async def tracked(session: AsyncSession, user_id: uuid.UUID) -> list[Habit]:
	return list(
		(
			await session.scalars(
				select(Habit)
				.where(Habit.user_id == user_id)
				.order_by(Habit.created_at, Habit.id)
			)
		).all()
	)


def suggestion(
	habits: list[Habit], sources: Sources, today: dt.date
) -> Suggestion | None:
	"""The one habit to suggest: something done on at least 3 different days in the
	last 3 weeks that isn't tracked or dismissed. Most recent days first."""
	known = {h.key for h in habits}
	floor = today - dt.timedelta(days=SUGGEST_WINDOW_DAYS - 1)
	keys = set(sources.activities) | {FAMILY, BIBLE}
	best: Suggestion | None = None
	for key in keys - known:
		dates = {d for d in sources.days_for(key).dates if d <= today}
		recent = sum(1 for d in dates if d >= floor)
		if recent < SUGGEST_MIN_DAYS:
			continue
		candidate = Suggestion(
			key=key,
			name=name_for(key),
			source=source_for(key),
			days=recent,
			total_days=len(dates),
		)
		if best is None or (candidate.days, candidate.total_days) > (
			best.days,
			best.total_days,
		):
			best = candidate
	return best


def nudge(habits: list[Habit], sources: Sources, today: dt.date) -> HabitPrompt | None:
	"""A habit with a target that's gone quiet for more than twice its usual gap (3 a
	week: 6 days). Only ever one, the quietest. Never for one not yet done."""
	best: tuple[float, HabitPrompt] | None = None
	for h in habits:
		if h.status != "active" or not h.target_per_week:
			continue
		dates = [d for d in sources.days_for(h.key).dates if d <= today]
		if not dates:
			continue
		last = max(dates)
		gap = (today - last).days
		allowed = 2 * math.ceil(7 / h.target_per_week)
		if gap <= allowed:
			continue
		ratio = gap / allowed
		prompt = HabitPrompt(
			kind="nudge",
			key=h.key,
			name=h.name,
			source=h.source,
			habit_id=h.id,
			last_done=last,
		)
		if best is None or ratio > best[0]:
			best = (ratio, prompt)
	return best[1] if best else None


async def week(
	session: AsyncSession, user: User, *, monday: dt.date
) -> list[HabitWeek]:
	"""Each tracked habit's days in the week starting `monday`."""
	habits = [h for h in await tracked(session, user.id) if h.status == "active"]
	if not habits:
		return []
	sources = await load_sources(session, user)
	sunday = monday + dt.timedelta(days=6)
	return [
		HabitWeek(
			habit_id=h.id,
			name=h.name,
			days=sum(1 for d in sources.days_for(h.key).dates if monday <= d <= sunday),
			target_per_week=h.target_per_week,
		)
		for h in habits
	]


# --- the backlog ----------------------------------------------------------------------

BACKLOG_DAYS = 60
BACKLOG_MAX_ATTEMPTS = 3

_UNREAD_FOR_HABITS = text(
	"""
	SELECT c.id, c.user_id
	FROM captures c JOIN users u ON u.id = c.user_id
	WHERE u.habits_enabled
		AND c.status = 'ready'
		AND c.text IS NOT NULL AND btrim(c.text) <> ''
		AND c.created_at > now() - make_interval(days => :days)
		AND NOT EXISTS (
			SELECT 1 FROM job_runs r
			WHERE r.capture_id = c.id AND r.stage = 'habits'
				AND (
					r.status = 'succeeded'
					OR r.attempt >= :max_attempts
					OR (r.status = 'running'
						AND r.started_at > now() - interval '15 minutes')
				)
		)
	ORDER BY c.created_at DESC
	LIMIT :limit
	"""
)


async def unread_for_habits(
	session: AsyncSession, *, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
	"""Recent notes of people with Habits on that the habits stage hasn't read, newest
	first: the history a habit brings with it when it's accepted."""
	rows = await session.execute(
		_UNREAD_FOR_HABITS,
		{"days": BACKLOG_DAYS, "max_attempts": BACKLOG_MAX_ATTEMPTS, "limit": limit},
	)
	return [(r[0], r[1]) for r in rows.all()]
