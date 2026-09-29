"""People (ADR 0029): reading who a note is about, and working out who they are.

Two halves with different failure costs, kept apart on purpose:

- **Reading** is a model call (PeopleReader): the names as written, whether the author
  was actually in touch, and when. Stateless - a note reads the same whatever the
  people table holds - so it is evaluated on its own and can run over old notes.
- **Resolving** is deterministic (`resolve`): which `people` row a name means. It
  never guesses between two candidates. Wrongly merging two real people corrupts
  both histories without anyone noticing; asking costs one tap. So it links only when
  exactly one person fits, creates a person when nobody does, and otherwise leaves the
  mention unlinked for the user to answer "which one?".
"""

import datetime as dt
import re
import unicodedata
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Literal, Protocol
from zoneinfo import ZoneInfo

import structlog
from anthropic import AsyncAnthropic
from sqlalchemy import Date, cast, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.config import Settings
from memo.ids import uuid7
from memo.models import Capture, Derivation, Person
from memo.prompts.people_v1 import (
	PEOPLE_VERSION,
	SYSTEM_PROMPT,
	repair_message,
	user_message,
)
from memo.schemas.derivations import MENTION, UNREVIEWED
from memo.schemas.people import (
	IN_TOUCH,
	MAX_ALIASES,
	MentionOut,
	MentionPayload,
	PeopleExtraction,
	PersonRef,
	ReachOut,
)
from memo.services.extraction import (
	AnthropicExtractionProvider,
	ExtractionProvider,
	complete_validated,
)
from memo.services.llm import LLMCall
from memo.services.retry import TerminalError

log = structlog.get_logger("memo.people")

STAGE = "people"
# Everyone memo meets starts on a monthly rhythm (Akin, 2026-09-28), editable per
# person. The clock runs from the last contact, so someone only ever mentioned -
# never met, called or messaged - is never suggested.
DEFAULT_REACH_OUT_DAYS = 30
# Today shows at most this many: a list of ten people to call is a chore, not a nudge.
REACH_OUT_LIMIT = 3
# Resolution is a check-then-insert per user (look for a person, create one if none);
# this advisory lock serialises it, so two notes naming a new "Kemi" processed at
# once make one Kemi, not two. Namespaced by the ADR number.
_LOCK_NAMESPACE = 29
# How alike two spellings must be to *ask* whether they are one person ("Tunday" /
# "Tunde"). Generous on purpose: a false positive costs a question, never a merge.
_SIMILAR = 0.6


# --- reading ----------------------------------------------------------------------


@dataclass(frozen=True)
class PeopleResult:
	extraction: PeopleExtraction
	raw: str
	calls: list[LLMCall]


class PeopleReaderLike(Protocol):
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
		author: str | None,
	) -> PeopleResult: ...


def _local_date(moment: dt.datetime, timezone: str) -> dt.date:
	try:
		return moment.astimezone(ZoneInfo(timezone)).date()
	except (KeyError, ValueError):
		return moment.astimezone(dt.UTC).date()


class PeopleReader:
	def __init__(self, provider: ExtractionProvider, *, max_repairs: int = 1) -> None:
		self._provider = provider
		self._max_repairs = max_repairs

	@property
	def model(self) -> str:
		return self._provider.model

	@property
	def version(self) -> str:
		return f"{PEOPLE_VERSION}/{self.model}"

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		author: str | None,
	) -> PeopleResult:
		captured_on = _local_date(captured_at, timezone)

		def parse(raw: str) -> PeopleExtraction:
			# The capture date goes in as validation context, so a "contact" dated
			# after the note was written fails into the repair loop.
			return PeopleExtraction.model_validate_json(
				raw, context={"captured_on": captured_on}
			)

		extraction, raw, calls = await complete_validated(
			self._provider,
			system=SYSTEM_PROMPT,
			first=user_message(
				text, captured_at=captured_at, timezone=timezone, author=author
			),
			parse=parse,
			repair=repair_message,
			stage=STAGE,
			max_repairs=self._max_repairs,
		)
		return PeopleResult(extraction=extraction, raw=raw, calls=calls)


class UnconfiguredPeopleReader:
	"""Stands in when no Anthropic key is set, so the stage fails visibly."""

	model = "unconfigured"
	version = f"{PEOPLE_VERSION}/unconfigured"

	async def read(
		self,
		text: str,
		*,
		captured_at: dt.datetime,
		timezone: str,
		author: str | None,
	) -> PeopleResult:
		raise TerminalError("the people stage is not configured (no ANTHROPIC_API_KEY)")


def make_people_reader(settings: Settings) -> PeopleReaderLike:
	if settings.anthropic_api_key is None:
		return UnconfiguredPeopleReader()
	client = AsyncAnthropic(
		api_key=settings.anthropic_api_key.get_secret_value(), max_retries=0
	)
	return PeopleReader(
		AnthropicExtractionProvider(
			client, model=settings.extraction_model, schema=PeopleExtraction
		)
	)


# --- resolving: pure functions, no database ------------------------------------------

_WORD = re.compile(r"[\w']+")


def normalize(name: str) -> str:
	"""Case, accents and punctuation don't make a different person: "Túndé" is
	"tunde", and "my mum" is "mum"."""
	decomposed = unicodedata.normalize("NFKD", name)
	plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
	words = _WORD.findall(plain.casefold())
	if len(words) > 1 and words[0] == "my":
		words = words[1:]
	return " ".join(words)


def _tokens(name: str) -> frozenset[str]:
	return frozenset(normalize(name).split())


def compatible(a: str, b: str) -> bool:
	"""Could these two names be the same person? One's words must all appear in the
	other's: "Tunde" fits "Tunde Bakare", "Bakare" fits it too, "Tunde Ade" does not."""
	ta, tb = _tokens(a), _tokens(b)
	return bool(ta) and bool(tb) and (ta <= tb or tb <= ta)


def _close(x: str, y: str) -> bool:
	return x[0] == y[0] and SequenceMatcher(None, x, y).ratio() >= _SIMILAR


def similar(a: str, b: str) -> bool:
	"""Spellings close enough to ask about ("Tunday" / "Tunde", "Ada" / "Adaeze"):
	`compatible`, with words allowed to be near misses. The first words (given
	names) must be close, and every word of the shorter name needs a close word in
	the longer. So "Tunde Ade" is not like "Tunde Bakare" (a different surname is
	evidence, not a typo), and not like "Ada" either (a surname that happens to be
	one letter off someone's first name). Used only to offer a candidate, never to
	link one."""
	wa, wb = normalize(a).split(), normalize(b).split()
	if not wa or not wb or not _close(wa[0], wb[0]):
		return False
	short, long = (wa, wb) if len(wa) <= len(wb) else (wb, wa)
	return all(any(_close(x, y) for y in long) for x in short)


def names_of(person: Person) -> list[str]:
	return [person.name, *(person.aliases or [])]


@dataclass(frozen=True)
class Resolution:
	"""link: exactly one person fits. ask: several could; `candidates` holds them.
	new: nobody fits, so this is someone memo hasn't met."""

	outcome: Literal["link", "ask", "new"]
	person_id: uuid.UUID | None = None
	candidates: list[uuid.UUID] = field(default_factory=list)


def _lookup_name(payload: MentionPayload) -> str:
	# The fuller name is the more specific one: "Tunde Ade" must not link to
	# "Tunde Bakare" just because both start with "Tunde".
	return payload.full_name or payload.name


def resolve(payload: MentionPayload, people: Sequence[Person]) -> Resolution:
	name = _lookup_name(payload)
	exact = [p for p in people if any(compatible(name, n) for n in names_of(p))]
	if len(exact) == 1:
		return Resolution("link", person_id=exact[0].id)
	if exact:
		return Resolution("ask", candidates=[p.id for p in exact])
	# A near spelling is a question, never an answer: "Tunday" may be a
	# transcription of "Tunde", or a different person.
	close = [p for p in people if any(similar(name, n) for n in names_of(p))]
	if close:
		return Resolution("ask", candidates=[p.id for p in close])
	return Resolution("new")


def with_alias(person: Person, name: str) -> list[str]:
	"""The person's aliases plus `name`, unless memo would already match it. Linking
	"Mum" to Folake teaches memo that "Mum" is Folake; linking "Tunde" to "Tunde
	Bakare" teaches it nothing it didn't know. Capped at MAX_ALIASES, the most a
	PATCH accepts, so a person merged many times can still be edited in the app."""
	aliases = list(person.aliases or [])
	if not normalize(name) or compatible(name, person.name):
		return aliases
	if len(aliases) >= MAX_ALIASES:
		return aliases
	if any(normalize(a) == normalize(name) for a in aliases):
		return aliases
	return [*aliases, name]


def new_person(user_id: uuid.UUID, payload: MentionPayload) -> Person:
	name = payload.full_name or payload.name
	person = Person(
		id=uuid7(),
		user_id=user_id,
		name=name,
		aliases=[],
		note=payload.hint,
		reach_out_every_days=DEFAULT_REACH_OUT_DAYS,
	)
	person.aliases = with_alias(person, payload.name)
	return person


def is_due(person: Person, last_contact: dt.date | None, today: dt.date) -> bool:
	if person.reach_out_every_days is None or last_contact is None:
		return False
	if person.snoozed_until is not None and person.snoozed_until > today:
		return False
	return last_contact + dt.timedelta(days=person.reach_out_every_days) <= today


# --- the database -------------------------------------------------------------------


async def lock_people(session: AsyncSession, user_id: uuid.UUID) -> None:
	"""Serialise changes to one user's people until this transaction ends."""
	await session.execute(
		text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
		{"ns": _LOCK_NAMESPACE, "uid": str(user_id)},
	)


async def load_people(session: AsyncSession, user_id: uuid.UUID) -> list[Person]:
	return list(
		(
			await session.scalars(
				select(Person)
				.where(Person.user_id == user_id)
				.order_by(Person.name, Person.id)
			)
		).all()
	)


# A note whose people read failed this many times is left alone: a note that fails
# every time would otherwise be paid for every ten minutes, forever.
BACKLOG_MAX_ATTEMPTS = 3

_UNREAD = """
	FROM captures c
	WHERE c.status = 'ready'
		AND c.text IS NOT NULL AND btrim(c.text) <> ''
		AND (CAST(:user_id AS uuid) IS NULL OR c.user_id = CAST(:user_id AS uuid))
		AND NOT EXISTS (
			SELECT 1 FROM job_runs r
			WHERE r.capture_id = c.id AND r.stage = 'people'
				AND (
					r.status = 'succeeded'
					OR r.attempt >= :max_attempts
					OR (r.status = 'running'
						AND r.started_at > now() - interval '15 minutes')
				)
		)
"""


async def unread_notes(
	session: AsyncSession, *, user_id: uuid.UUID | None = None, limit: int
) -> list[tuple[uuid.UUID, uuid.UUID]]:
	"""Ready notes the people stage has never read (any version), newest first, as
	(capture_id, user_id). A version bump does not re-read history, as with
	extraction: re-reading would duplicate what the user already confirmed."""
	rows = await session.execute(
		text(
			f"SELECT c.id, c.user_id {_UNREAD} ORDER BY c.created_at DESC LIMIT :limit"
		),
		{
			"user_id": str(user_id) if user_id else None,
			"max_attempts": BACKLOG_MAX_ATTEMPTS,
			"limit": limit,
		},
	)
	return [(r[0], r[1]) for r in rows.all()]


async def count_unreadable_notes(session: AsyncSession, user_id: uuid.UUID) -> int:
	"""Notes the people stage gave up on after BACKLOG_MAX_ATTEMPTS real failures.
	Shown, not dropped: a note that quietly left the "to go" count would look read."""
	count = await session.scalar(
		text(
			"SELECT count(DISTINCT r.capture_id) FROM job_runs r "
			"WHERE r.user_id = CAST(:user_id AS uuid) AND r.stage = 'people' "
			"AND r.status = 'failed' AND r.attempt >= :max_attempts "
			"AND NOT EXISTS (SELECT 1 FROM job_runs ok "
			"WHERE ok.capture_id = r.capture_id "
			"AND ok.stage = 'people' AND ok.status = 'succeeded')"
		),
		{"user_id": str(user_id), "max_attempts": BACKLOG_MAX_ATTEMPTS},
	)
	return int(count or 0)


async def count_unread_notes(session: AsyncSession, user_id: uuid.UUID) -> int:
	count = await session.scalar(
		text(f"SELECT count(*) {_UNREAD}"),
		{"user_id": str(user_id), "max_attempts": BACKLOG_MAX_ATTEMPTS},
	)
	return int(count or 0)


async def owned_person(
	session: AsyncSession, user_id: uuid.UUID, person_id: uuid.UUID
) -> Person | None:
	return await session.scalar(
		select(Person).where(Person.id == person_id, Person.user_id == user_id)
	)


async def apply_mentions(
	session: AsyncSession,
	capture: Capture,
	extraction: PeopleExtraction,
	*,
	version: str,
	timezone: str,
	auto_accept: bool = False,
) -> list[Derivation]:
	"""Write one `mention` per person the note names, each linked, new, or waiting
	for the user. Idempotent by replacement, like extraction: a re-run of this version
	replaces only what nobody has reviewed. Returns the mentions written.

	With auto-accept (ADR 0030) a linked mention is kept as 'accepted'. One that could
	be two people still waits: the setting says "trust the reading", not "guess who"."""
	user_id = capture.user_id
	await lock_people(session, user_id)
	stale = (
		await session.execute(
			delete(Derivation)
			.where(
				Derivation.capture_id == capture.id,
				Derivation.user_id == user_id,
				Derivation.kind == MENTION,
				Derivation.extractor_version == version,
				Derivation.status.in_(UNREVIEWED),
			)
			.returning(Derivation.person_id)
		)
	).all()
	await prune_if_empty(session, user_id, [r[0] for r in stale if r[0] is not None])

	# Everyone as they were before this note: two new "Tunde"s in one note are two
	# people, so a person created a few lines up is not a candidate for the next.
	known = await load_people(session, user_id)
	captured_on = _local_date(capture.created_at, timezone)
	written: dict[uuid.UUID, Derivation] = {}  # person -> this note's mention
	out: list[Derivation] = []
	for item in extraction.people:
		payload = item.model_dump(mode="json", exclude={"confidence"})
		if item.contact in IN_TOUCH and item.occurred_on is None:
			payload["occurred_on"] = captured_on.isoformat()
		resolution = resolve(item, known)
		person_id = resolution.person_id
		if resolution.outcome == "new":
			person = new_person(user_id, item)
			session.add(person)
			await session.flush()
			person_id = person.id
		if person_id is not None and person_id in written:
			# The same person twice in one note: keep one mention, and keep the
			# contact if either says they were in touch.
			kept = written[person_id]
			if item.contact in IN_TOUCH and kept.payload["contact"] not in IN_TOUCH:
				kept.payload = payload
			continue
		mention = Derivation(
			id=uuid7(),
			user_id=user_id,
			capture_id=capture.id,
			kind=MENTION,
			payload=payload,
			status="accepted"
			if auto_accept and person_id is not None
			else "pending_review",
			extractor_version=version,
			confidence=item.confidence,
			person_id=person_id,
		)
		session.add(mention)
		out.append(mention)
		if person_id is not None:
			written[person_id] = mention
	await session.flush()
	return out


# Which of two mentions of one person in one note to keep: the more trusted review.
_REVIEW_RANK = {"user_created": 0, "confirmed": 1, "accepted": 2, "pending_review": 3}


async def collapse_duplicates(
	session: AsyncSession, user_id: uuid.UUID, person_id: uuid.UUID
) -> set[uuid.UUID]:
	"""One mention per person per note, as apply_mentions writes them. A merge, or
	linking a mention to someone the note already names, can make two; this keeps the
	one that says they were in touch (else the more trusted review), gives it the best
	review of the group, and deletes the rest. Returns the ids deleted."""
	rows = (
		await session.scalars(
			select(Derivation)
			.where(
				Derivation.user_id == user_id,
				Derivation.person_id == person_id,
				Derivation.kind == MENTION,
				Derivation.status != "rejected",
				Derivation.capture_id.is_not(None),
			)
			.order_by(Derivation.created_at, Derivation.id)
		)
	).all()
	by_note: dict[uuid.UUID, list[Derivation]] = {}
	for d in rows:
		assert d.capture_id is not None
		by_note.setdefault(d.capture_id, []).append(d)
	deleted: set[uuid.UUID] = set()
	for group in by_note.values():
		if len(group) < 2:
			continue

		def rank(d: Derivation) -> tuple[bool, int]:
			return (
				d.payload.get("contact") not in IN_TOUCH,
				_REVIEW_RANK.get(d.status, 9),
			)

		keep = min(group, key=rank)
		best = min((d.status for d in group), key=lambda st: _REVIEW_RANK.get(st, 9))
		if _REVIEW_RANK.get(best, 9) < _REVIEW_RANK.get(keep.status, 9):
			keep.status = best
		for d in group:
			if d is not keep:
				await session.delete(d)
				deleted.add(d.id)
	if deleted:
		await session.flush()
		log.info("people.collapsed_duplicates", removed=len(deleted))
	return deleted


async def prune_if_empty(
	session: AsyncSession, user_id: uuid.UUID, person_ids: Iterable[uuid.UUID]
) -> int:
	"""Delete any of these people with no mention left that isn't rejected. A person
	exists because notes name them; one whose every mention was rejected (a misheard
	name, "that's not a person") would otherwise linger as a phantom candidate."""
	ids = set(person_ids)
	if not ids:
		return 0
	still_named = (
		select(Derivation.person_id)
		.where(
			Derivation.user_id == user_id,
			Derivation.person_id.in_(ids),
			Derivation.status != "rejected",
		)
		.distinct()
	)
	result = await session.execute(
		delete(Person)
		.where(
			Person.user_id == user_id,
			Person.id.in_(ids),
			Person.id.not_in(still_named),
		)
		.returning(Person.id)
	)
	removed = len(result.all())
	if removed:
		log.info("people.pruned", removed=removed)
	return removed


async def last_contacts(
	session: AsyncSession, user_id: uuid.UUID
) -> dict[uuid.UUID, dt.date]:
	"""Each person's last contact, computed rather than stored: a stored date would
	have to follow every review, edit, merge and rejection, and would drift."""
	occurred = cast(Derivation.payload["occurred_on"].astext, Date)
	rows = await session.execute(
		select(Derivation.person_id, func.max(occurred))
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == MENTION,
			Derivation.status != "rejected",
			Derivation.person_id.is_not(None),
			Derivation.payload["contact"].astext.in_(sorted(IN_TOUCH)),
			Derivation.payload["occurred_on"].astext.is_not(None),
		)
		.group_by(Derivation.person_id)
	)
	return {pid: day for pid, day in rows.all() if pid is not None and day}


async def mention_counts(
	session: AsyncSession, user_id: uuid.UUID
) -> dict[uuid.UUID, int]:
	rows = await session.execute(
		select(Derivation.person_id, func.count())
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == MENTION,
			Derivation.status != "rejected",
			Derivation.person_id.is_not(None),
		)
		.group_by(Derivation.person_id)
	)
	return {pid: n for pid, n in rows.all() if pid is not None}


@dataclass(frozen=True)
class Timed:
	"""A mention with when its note was written (the capture's time, not the row's:
	a mention read from an old note today belongs on the old note's date)."""

	mention: Derivation
	written_at: dt.datetime | None
	excerpt: str | None

	def on(self, zone: ZoneInfo) -> dt.date:
		occurred = self.mention.payload.get("occurred_on")
		if occurred:
			return dt.date.fromisoformat(occurred)
		moment = self.written_at or self.mention.created_at
		return moment.astimezone(zone).date()


async def timeline(
	session: AsyncSession,
	user_id: uuid.UUID,
	person_ids: Sequence[uuid.UUID],
	zone: ZoneInfo,
) -> dict[uuid.UUID, list[Timed]]:
	"""Each person's live mentions with their notes, newest first."""
	if not person_ids:
		return {}
	rows = await session.execute(
		select(Derivation, Capture.created_at, Capture.text)
		.outerjoin(
			Capture,
			(Capture.id == Derivation.capture_id)
			& (Capture.user_id == Derivation.user_id),
		)
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == MENTION,
			Derivation.status != "rejected",
			Derivation.person_id.in_(person_ids),
		)
	)
	out: dict[uuid.UUID, list[Timed]] = {}
	for d, written_at, body in rows.all():
		excerpt = " ".join(body.split())[:200] if body else None
		assert d.person_id is not None
		out.setdefault(d.person_id, []).append(Timed(d, written_at, excerpt))
	for entries in out.values():
		entries.sort(
			key=lambda t: (t.on(zone), t.written_at or t.mention.created_at),
			reverse=True,
		)
	return out


async def reach_out(
	session: AsyncSession,
	user_id: uuid.UUID,
	*,
	today: dt.date,
	zone: ZoneInfo,
	limit: int = REACH_OUT_LIMIT,
) -> list[ReachOut]:
	"""The few people most past their rhythm, each with the latest thing a note said
	about them. Ranked by how far past (a month-rhythm friend at six weeks comes
	before a weekly one at eight days), and capped: no counts, no backlog."""
	people = [
		p
		for p in await load_people(session, user_id)
		if p.reach_out_every_days is not None
	]
	if not people:
		return []
	lasts = await last_contacts(session, user_id)
	due = [p for p in people if is_due(p, lasts.get(p.id), today)]

	def overdue(p: Person) -> float:
		assert p.reach_out_every_days is not None
		return (today - lasts[p.id]).days / p.reach_out_every_days

	due.sort(key=lambda p: (-overdue(p), p.name))
	due = due[:limit]
	history = await timeline(session, user_id, [p.id for p in due], zone)
	out: list[ReachOut] = []
	for p in due:
		assert p.reach_out_every_days is not None
		latest = next(
			(t for t in history.get(p.id, []) if t.mention.payload.get("context")),
			None,
		)
		out.append(
			ReachOut(
				person_id=p.id,
				name=p.name,
				last_contact_on=lasts[p.id],
				every_days=p.reach_out_every_days,
				latest=latest.mention.payload.get("context") if latest else None,
				latest_on=latest.on(zone) if latest else None,
			)
		)
	return out


def person_ref(person: Person, lasts: dict[uuid.UUID, dt.date]) -> PersonRef:
	return PersonRef(
		id=person.id,
		name=person.name,
		note=person.note,
		last_contact_on=lasts.get(person.id),
	)


def mention_out(
	d: Derivation,
	*,
	people: dict[uuid.UUID, Person],
	lasts: dict[uuid.UUID, dt.date],
	written_at: dt.datetime | None = None,
	excerpt: str | None = None,
) -> MentionOut:
	"""A mention for the app. An unlinked one still awaiting review carries the
	people it could be, worked out now rather than stored: a person added or merged
	since the note was read changes the answer."""
	payload = MentionPayload.model_validate(d.payload)
	person = people.get(d.person_id) if d.person_id is not None else None
	candidates: list[PersonRef] = []
	if person is None and d.status == "pending_review":
		resolution = resolve(payload, list(people.values()))
		ids = (
			[resolution.person_id]
			if resolution.person_id is not None
			else resolution.candidates
		)
		candidates = [person_ref(people[i], lasts) for i in ids if i in people]
	return MentionOut(
		id=d.id,
		capture_id=d.capture_id,
		status=d.status,
		confidence=d.confidence,
		name=payload.name,
		full_name=payload.full_name,
		hint=payload.hint,
		contact=payload.contact,
		occurred_on=payload.occurred_on,
		context=payload.context,
		person=person_ref(person, lasts) if person is not None else None,
		candidates=candidates,
		created_at=d.created_at,
		written_at=written_at,
		excerpt=excerpt,
	)
