"""Lately (ADR 0062): a line or two at the top of a person's page on what's been going
on with them, written from the notes that mention them.

Written ahead, never on the page's request: a sweep finds people whose line is out of
date and rewrites it, so the page opens at once and a note is never slowed down by it.
A line is out of date when their live mentions changed (a new note, a deleted one, a
mention relinked or rejected) or the person's tone did. The line is in their tone and
has no em dashes (ADR 0061).
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Protocol

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.models import Person, User
from memo.prompts.lately_v1 import (
	LATELY_VERSION,
	Mentioned,
	repair_message,
	system,
	user_message,
)
from memo.prompts.voice import as_tone
from memo.schemas.people import IN_TOUCH, LatelyWrite
from memo.services.extraction import ExtractionProvider, complete_validated
from memo.services.llm import LLMCall
from memo.services.morning import tz_or_utc
from memo.services.people import timeline
from memo.services.prose import plain_dashes
from memo.services.providers import structured_provider
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.lately")

# The newest notes that mention someone: enough for "lately", few enough that last
# year's news can't crowd out this week's.
NOTES_READ = 6

_TOUCH = {
	"in_person": "in touch in person",
	"call": "spoke on the phone",
	"message": "messaged",
	"unspecified": "in touch",
}


@dataclass(frozen=True)
class LatelyResult:
	written: LatelyWrite
	raw: str
	calls: list[LLMCall]


class LatelyWriterLike(Protocol):
	@property
	def version(self) -> str: ...

	async def write(
		self, name: str, today: dt.date, notes: list[Mentioned], *, tone: str | None
	) -> LatelyResult: ...


class LatelyWriter:
	def __init__(self, provider: ExtractionProvider, *, max_repairs: int = 1):
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def version(self) -> str:
		return f"{LATELY_VERSION}/{self._provider.model}"

	async def write(
		self, name: str, today: dt.date, notes: list[Mentioned], *, tone: str | None
	) -> LatelyResult:
		written, raw, calls = await complete_validated(
			self._provider,
			system=system(tone),
			first=user_message(name, today, notes),
			parse=LatelyWrite.model_validate_json,
			repair=repair_message,
			stage="lately",
			max_repairs=self._max_repairs,
		)
		return LatelyResult(written=written, raw=raw, calls=calls)


class UnconfiguredLatelyWriter:
	"""Stands in when the model's API key is unset: the sweep leaves lines unwritten."""

	version = f"{LATELY_VERSION}/unconfigured"

	async def write(
		self, name: str, today: dt.date, notes: list[Mentioned], *, tone: str | None
	) -> LatelyResult:
		raise TerminalError("Lately is not configured (no API key for its model)")


def make_lately_writer(settings: Settings) -> LatelyWriterLike:
	provider = structured_provider(
		settings, model=settings.lately_model, schema=LatelyWrite
	)
	if provider is None:
		return UnconfiguredLatelyWriter()
	return LatelyWriter(provider)


# --- what's out of date --------------------------------------------------------------

_STALE = text(
	"""
	SELECT p.id, p.user_id
	FROM people p
	JOIN users u ON u.id = p.user_id
	LEFT JOIN LATERAL (
		SELECT count(*) AS n, max(d.updated_at) AS newest
		FROM derivations d
		WHERE d.user_id = p.user_id AND d.person_id = p.id
			AND d.kind = 'mention' AND d.status <> 'rejected'
	) m ON true
	WHERE (
		m.n > 0 AND (
			p.lately_at IS NULL
			OR m.newest > p.lately_at
			OR m.n <> p.lately_count
			OR p.lately_tone IS DISTINCT FROM u.tone
		)
	) OR (m.n = 0 AND p.lately_at IS NOT NULL)
	ORDER BY m.newest DESC NULLS LAST
	LIMIT :limit
	"""
)


async def stale_people(
	session: AsyncSession, *, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
	"""People whose line doesn't match their notes any more, most recently mentioned
	first. Across users: run it as the system."""
	rows = await session.execute(_STALE, {"limit": limit})
	return [(r[0], r[1]) for r in rows.all()]


# --- writing one ---------------------------------------------------------------------


async def refresh_lately(
	session: AsyncSession, person: Person, user: User, writer: LatelyWriterLike
) -> list[LLMCall]:
	"""Rewrite one person's line from their newest notes, or clear it when there are
	none. Stamps what it was written from, so the sweep leaves it alone until that
	changes. The caller commits; a failure leaves the row as it was."""
	zone = tz_or_utc(user.timezone)
	entries = (await timeline(session, user.id, [person.id], zone)).get(person.id, [])
	tone = as_tone(user.tone)
	now = dt.datetime.now(dt.UTC)
	calls: list[LLMCall] = []
	if not entries:
		person.lately = None
	else:
		notes = [
			Mentioned(
				on=t.on(zone),
				in_touch=_TOUCH.get(t.mention.payload.get("contact", ""))
				if t.mention.payload.get("contact") in IN_TOUCH
				else None,
				about=t.mention.payload.get("context"),
				opening=t.excerpt[:140] if t.excerpt else None,
			)
			for t in entries[:NOTES_READ]
		]
		result = await writer.write(
			person.name, dt.datetime.now(zone).date(), notes, tone=tone
		)
		calls = result.calls
		line = plain_dashes(result.written.text).strip()
		person.lately = line or None
	person.lately_at = now
	person.lately_count = len(entries)
	person.lately_tone = tone
	person.lately_version = writer.version
	log.info(
		"lately.written",
		person_id=str(person.id),
		notes=min(len(entries), NOTES_READ),
		empty=person.lately is None,
	)
	return calls


async def mark_unwritten(
	session: AsyncSession, person: Person, user_id: uuid.UUID
) -> None:
	"""After a failure that won't fix itself: stamp the row as up to date without a new
	line, so the sweep doesn't pay to fail again until their notes change. Whatever
	line was there stays."""
	user = await session.get(User, user_id)
	count = await session.scalar(
		text(
			"SELECT count(*) FROM derivations WHERE user_id = :u AND person_id = :p "
			"AND kind = 'mention' AND status <> 'rejected'"
		),
		{"u": user_id, "p": person.id},
	)
	person.lately_at = dt.datetime.now(dt.UTC)
	person.lately_count = int(count or 0)
	person.lately_tone = as_tone(user.tone if user is not None else None)
