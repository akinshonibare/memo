"""Mood (ADR 0058): how you sounded in your notes, for someone with Within (Faith) on.

A reader looks at each note and says how the author sounds in it, on five steps from
low to bright, with the phrase that shows it - or, usually, that the note doesn't say.
Days are made from those reads when asked for, never stored: your own word for a day
(`mood_day`) outranks every read of it, and a note deleted takes its read with it.

memo describes how notes sound. It never scores, diagnoses or nudges anyone about it:
there is no streak, no alert, and nothing leaves the Within screen.
"""

import datetime as dt
import math
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import and_, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.ids import uuid7
from memo.models import Capture, Derivation, User
from memo.prompts.mood_v1 import (
	MOOD_VERSION,
	SYSTEM_PROMPT,
	repair_message,
	user_message,
)
from memo.schemas.derivations import MOOD, MOOD_DAY
from memo.schemas.mood import (
	MoodDayDetail,
	MoodDayOut,
	MoodRead,
	MoodReadOut,
	MoodWeekOut,
)
from memo.services.extraction import ExtractionProvider, complete_validated
from memo.services.faith import faith_on
from memo.services.llm import LLMCall
from memo.services.mindmap import note_label
from memo.services.morning import local_day_range, tz_or_utc
from memo.services.providers import structured_provider
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.mood")

WEEK_DAYS = 7
MAX_DAYS = 60
# A read memo isn't sure of still counts, just for less: a day of one unsure "heavy"
# and one sure "good" leans good.
MIN_WEIGHT = 0.3

NAMES = {-2: "low", -1: "heavy", 0: "steady", 1: "good", 2: "bright"}


def mood_on(user: User | None) -> bool:
	"""Mood lives in Within, so it is on exactly when Faith is (Akin, 2026-09-30)."""
	return faith_on(user)


# --- the reader ---------------------------------------------------------------------


@dataclass(frozen=True)
class MoodResult:
	read: MoodRead
	raw: str
	calls: list[LLMCall]


class MoodReaderLike(Protocol):
	@property
	def model(self) -> str: ...

	@property
	def version(self) -> str: ...

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> MoodResult: ...


class MoodReader:
	def __init__(self, provider: ExtractionProvider, *, max_repairs: int = 1):
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{MOOD_VERSION}/{self.model}"

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> MoodResult:
		read, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=user_message(text, captured_at=captured_at, timezone=timezone),
			parse=MoodRead.model_validate_json,
			repair=repair_message,
			stage="mood",
			max_repairs=self._max_repairs,
		)
		return MoodResult(read=read, raw=raw, calls=calls)


class UnconfiguredMoodReader:
	"""Stands in when the model's API key is unset, so the stage fails visibly."""

	model = "unconfigured"
	version = f"{MOOD_VERSION}/unconfigured"

	async def read(
		self, text: str, *, captured_at: dt.datetime, timezone: str
	) -> MoodResult:
		raise TerminalError(
			"the mood stage is not configured (no API key for its model)"
		)


def make_mood_reader(settings: Settings) -> MoodReaderLike:
	provider = structured_provider(settings, model=settings.mood_model, schema=MoodRead)
	if provider is None:
		return UnconfiguredMoodReader()
	return MoodReader(provider)


def _squash(value: str) -> str:
	return " ".join(value.split()).casefold()


def quoted(evidence: str | None, source: str) -> str | None:
	"""The evidence, only if the note really says it. A phrase the reader paraphrased
	or made up is dropped: the read stays, but nothing is put in the author's mouth."""
	if evidence and _squash(evidence) in _squash(source):
		return evidence
	return None


async def apply_mood(
	session: AsyncSession, capture: Capture, read: MoodRead, *, version: str
) -> Derivation | None:
	"""File one note's read. Idempotent by replacement: a note has at most one read,
	and reading it again replaces it. Nothing is kept when the note shows no mood."""
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == capture.user_id,
			Derivation.kind == MOOD,
		)
	)
	if read.valence is None:
		return None
	evidence = quoted(read.evidence, capture.text or "")
	if read.evidence and evidence is None:
		log.info("mood.evidence_not_in_note", capture_id=str(capture.id))
	row = Derivation(
		id=uuid7(),
		user_id=capture.user_id,
		capture_id=capture.id,
		kind=MOOD,
		payload={"valence": read.valence, "words": read.words, "evidence": evidence},
		# Never reviewed item by item: your word for the day is the correction.
		status="accepted",
		extractor_version=version,
		confidence=read.confidence,
	)
	session.add(row)
	return row


# --- days ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Read:
	capture: Capture
	valence: int
	words: list[str]
	evidence: str | None
	weight: float


def _round(value: float) -> int:
	"""Half away from zero, so a day halfway between heavy and steady says heavy, and
	halfway between steady and good says good: never quietly steady."""
	return int(math.copysign(math.floor(abs(value) + 0.5), value))


def _day_of(reads: Sequence[_Read], yours: int | None, day: dt.date) -> MoodDayOut:
	words = Counter(w for r in reads for w in r.words)
	top = [w for w, _ in words.most_common(3)]
	if yours is not None:
		return MoodDayOut(
			day=day, valence=yours, yours=True, words=top, notes=len(reads)
		)
	if not reads:
		return MoodDayOut(day=day, valence=None, yours=False, words=[], notes=0)
	total = sum(r.weight for r in reads)
	mean = sum(r.valence * r.weight for r in reads) / total
	return MoodDayOut(
		day=day, valence=_round(mean), yours=False, words=top, notes=len(reads)
	)


async def _reads_between(
	session: AsyncSession, user: User, first: dt.date, last: dt.date
) -> dict[dt.date, list[_Read]]:
	zone = tz_or_utc(user.timezone)
	start, _ = local_day_range(first, zone)
	_, end = local_day_range(last, zone)
	rows = (
		await session.execute(
			select(Derivation, Capture)
			.join(
				Capture,
				and_(
					Capture.id == Derivation.capture_id,
					Capture.user_id == Derivation.user_id,
				),
			)
			.where(
				Derivation.user_id == user.id,
				Derivation.kind == MOOD,
				Derivation.status != "rejected",
				Capture.created_at >= start,
				Capture.created_at < end,
			)
			.order_by(Capture.created_at, Capture.id)
		)
	).all()
	by_day: dict[dt.date, list[_Read]] = {}
	for d, capture in rows:
		p = d.payload
		by_day.setdefault(capture.created_at.astimezone(zone).date(), []).append(
			_Read(
				capture=capture,
				valence=int(p["valence"]),
				words=list(p.get("words") or []),
				evidence=p.get("evidence"),
				weight=max(MIN_WEIGHT, d.confidence if d.confidence is not None else 1),
			)
		)
	return by_day


async def _yours_between(
	session: AsyncSession, user_id: uuid.UUID, first: dt.date, last: dt.date
) -> dict[dt.date, int]:
	rows = (
		await session.scalars(
			select(Derivation).where(
				Derivation.user_id == user_id,
				Derivation.kind == MOOD_DAY,
				Derivation.payload["day"].astext >= first.isoformat(),
				Derivation.payload["day"].astext <= last.isoformat(),
			)
		)
	).all()
	return {
		dt.date.fromisoformat(d.payload["day"]): int(d.payload["valence"]) for d in rows
	}


def today_for(user: User) -> dt.date:
	return dt.datetime.now(tz_or_utc(user.timezone)).date()


async def mood_days(
	session: AsyncSession, user: User, *, days: int, today: dt.date
) -> list[MoodDayOut]:
	"""The last `days` days, oldest first, ending `today`."""
	first = today - dt.timedelta(days=days - 1)
	reads = await _reads_between(session, user, first, today)
	yours = await _yours_between(session, user.id, first, today)
	return [
		_day_of(reads.get(day, []), yours.get(day), day)
		for day in (first + dt.timedelta(days=n) for n in range(days))
	]


_WEEKDAY = (
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
)


def _names(days: Sequence[dt.date]) -> str:
	said = [_WEEKDAY[d.weekday()] for d in days]
	if len(said) == 1:
		return said[0]
	return ", ".join(said[:-1]) + " and " + said[-1]


def summary(days: Sequence[MoodDayOut]) -> str | None:
	"""One sentence about a run of days, from the days alone: no model call, so it is
	cheap, the same every time, and never says more than the dots do.

	A week that went both ways (two days or more on each side of steady) is
	"up-and-down", never averaged into "steady". Otherwise the mean names the week, and
	only days on the far side of steady from it are named: a steady Thursday is not
	"heavier" in a good week."""
	said = [(d.day, d.valence) for d in days if d.valence is not None]
	if not said:
		return None
	if len(said) < 3:
		return "Only a day or two so far. There'll be a week to see soon."
	brighter = [day for day, v in said if v >= 1]
	heavier = [day for day, v in said if v <= -1]
	if len(brighter) >= 2 and len(heavier) >= 2:
		return (
			f"An up-and-down week. Brighter on {_names(brighter)}."
			f" Heavier on {_names(heavier)}."
		)
	overall = _round(sum(v for _, v in said) / len(said))
	sentence = f"A {NAMES[overall]} week."
	if overall <= 0 and brighter:
		sentence += f" Brighter on {_names(brighter)}."
	if overall >= 0 and heavier:
		sentence += f" Heavier on {_names(heavier)}."
	return sentence


async def mood_week(session: AsyncSession, user: User) -> MoodWeekOut:
	days = await mood_days(session, user, days=WEEK_DAYS, today=today_for(user))
	return MoodWeekOut(days=days, summary=summary(days))


async def day_detail(session: AsyncSession, user: User, day: dt.date) -> MoodDayDetail:
	reads = (await _reads_between(session, user, day, day)).get(day, [])
	yours = (await _yours_between(session, user.id, day, day)).get(day)
	return MoodDayDetail(
		day=_day_of(reads, yours, day),
		reads=[
			MoodReadOut(
				capture_id=r.capture.id,
				title=note_label(r.capture),
				created_at=r.capture.created_at,
				valence=r.valence,
				words=r.words,
				evidence=r.evidence,
			)
			for r in reads
		],
	)


async def set_day(
	session: AsyncSession, user_id: uuid.UUID, day: dt.date, valence: int
) -> None:
	"""Your word for a day. Saying again replaces it (one per day, a unique index)."""
	await clear_day(session, user_id, day)
	session.add(
		Derivation(
			id=uuid7(),
			user_id=user_id,
			capture_id=None,
			kind=MOOD_DAY,
			payload={"day": day.isoformat(), "valence": valence},
			status="user_created",
			extractor_version="user",
			confidence=None,
		)
	)


async def clear_day(session: AsyncSession, user_id: uuid.UUID, day: dt.date) -> None:
	"""Back to memo's reads for that day."""
	await session.execute(
		delete(Derivation).where(
			Derivation.user_id == user_id,
			Derivation.kind == MOOD_DAY,
			Derivation.payload["day"].astext == day.isoformat(),
		)
	)


# --- the backlog ----------------------------------------------------------------------

BACKLOG_DAYS = 30
BACKLOG_MAX_ATTEMPTS = 3

_UNREAD_FOR_MOOD = text(
	"""
	SELECT c.id, c.user_id
	FROM captures c JOIN users u ON u.id = c.user_id
	WHERE u.faith_enabled
		AND c.status = 'ready'
		AND c.text IS NOT NULL AND btrim(c.text) <> ''
		AND c.created_at > now() - make_interval(days => :days)
		AND NOT EXISTS (
			SELECT 1 FROM job_runs r
			WHERE r.capture_id = c.id AND r.stage = 'mood'
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


async def unread_for_mood(
	session: AsyncSession, *, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
	"""Recent notes of people with Within on that the mood stage hasn't read, newest
	first: the month the Within screen shows as soon as it's turned on."""
	rows = await session.execute(
		_UNREAD_FOR_MOOD,
		{"days": BACKLOG_DAYS, "max_attempts": BACKLOG_MAX_ATTEMPTS, "limit": limit},
	)
	return [(r[0], r[1]) for r in rows.all()]
