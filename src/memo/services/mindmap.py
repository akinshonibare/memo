"""The map of one thing (ADR 0049): what memo has already linked to a person, a note,
an idea or a prayer, as the neighbours of that one thing.

No new data and no model: every edge is a link memo already keeps. Who a note is about
(mentions, ADR 0029), what was taken from it (items, prayers, passages), notes about
the same thing (related, ADR 0028), and what a note answers or carries on (ADR 0048).
One node in the middle is the design: a whole-graph view of a journal is a hairball,
and the question people ask of a map is "what's connected to *this*?".
"""

import datetime as dt
import uuid
from collections import Counter
from collections.abc import Iterable, Sequence
from itertools import zip_longest
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from memo.models import Capture, Derivation, Person, User
from memo.schemas.derivations import MENTION, PASSAGE, PRAYER, UPDATE
from memo.schemas.mindmap import CenterKind, MapLink, MapNode, MapOut
from memo.services.faith import faith_hidden, passage_out
from memo.services.morning import tz_or_utc

# At most this many around the middle: more and the labels collide on a phone.
MAX_LINKS = 14
LABEL_CHARS = 60


class NotFound(Exception):
	"""No such thing, or not this user's."""


# --- nodes -----------------------------------------------------------------------


def note_label(capture: Capture) -> str:
	body = " ".join((capture.text or "").split())
	if not body:
		return {"audio": "Voice note", "image": "Photo"}.get(capture.kind, "Note")
	return body if len(body) <= LABEL_CHARS else body[: LABEL_CHARS - 1].rstrip() + "…"


def note_node(capture: Capture, zone: ZoneInfo) -> MapNode:
	return MapNode(
		kind="note",
		id=str(capture.id),
		label=note_label(capture),
		when=capture.created_at.astimezone(zone).date(),
		capture_id=capture.id,
		centrable=True,
	)


def person_node(person: Person) -> MapNode:
	return MapNode(
		kind="person",
		id=str(person.id),
		label=person.name,
		detail=person.note,
		centrable=True,
	)


def item_node(d: Derivation, zone: ZoneInfo) -> MapNode:
	p = d.payload
	title = str(p.get("title", ""))
	if d.kind == PASSAGE:
		passage = passage_out(d)
		return MapNode(
			kind="passage", id=passage.ref, label=passage.ref, passage=passage
		)
	when: dt.date | None = None
	detail: str | None = None
	try:
		if d.kind == "event" and p.get("starts_at"):
			when = dt.datetime.fromisoformat(p["starts_at"]).astimezone(zone).date()
		elif d.kind == "todo" and p.get("due"):
			when = dt.date.fromisoformat(p["due"])
	except (TypeError, ValueError):
		when = None
	if d.kind == "idea":
		detail = p.get("summary")
	elif d.kind == PRAYER:
		detail = "answered" if d.completed_at is not None else None
	kind = d.kind if d.kind in ("idea", "prayer", "todo", "event") else "idea"
	return MapNode(
		kind=kind,
		id=str(d.id),
		label=title,
		detail=detail,
		when=when,
		capture_id=d.capture_id,
		centrable=d.kind in ("idea", PRAYER),
	)


# --- queries -----------------------------------------------------------------------


_RELATED = text(
	"""
	WITH this AS (
		SELECT avg(embedding) AS centroid FROM chunks
		WHERE capture_id = :cid AND user_id = :uid
			AND derivation_id IS NULL AND superseded_at IS NULL
	)
	SELECT k.capture_id, min(k.embedding <=> this.centroid) AS distance
	FROM chunks k, this
	WHERE this.centroid IS NOT NULL
		AND k.user_id = :uid AND k.capture_id <> :cid AND k.capture_id IS NOT NULL
		AND k.derivation_id IS NULL AND k.superseded_at IS NULL
		AND (CAST(:space AS text) IS NULL OR k.space = :space)
	GROUP BY k.capture_id
	ORDER BY distance
	LIMIT :n
	"""
)


async def related_notes(
	session: AsyncSession,
	user_id: uuid.UUID,
	capture_id: uuid.UUID,
	*,
	space: str | None,
	limit: int,
	max_distance: float,
) -> list[tuple[uuid.UUID, float]]:
	"""Notes about the same thing (ADR 0028): nearest to this note's embedding centroid,
	under the threshold. (capture id, distance), nearest first."""
	rows = (
		await session.execute(
			_RELATED,
			{"cid": capture_id, "uid": user_id, "n": limit, "space": space},
		)
	).all()
	return [
		(cid, float(distance))
		for cid, distance in rows
		if distance is not None and distance <= max_distance
	]


_NEAR_IDEAS = text(
	"""
	SELECT d.id, min(k.embedding <=> o.embedding) AS distance
	FROM chunks o
	JOIN chunks k
		ON k.user_id = o.user_id AND k.superseded_at IS NULL
		AND k.derivation_id IS NOT NULL AND k.derivation_id <> :idea_id
	JOIN derivations d ON d.id = k.derivation_id AND d.user_id = k.user_id
	WHERE o.user_id = :uid AND o.derivation_id = :idea_id AND o.superseded_at IS NULL
		AND d.kind = 'idea' AND d.status <> 'rejected'
	GROUP BY d.id
	HAVING min(k.embedding <=> o.embedding) <= :max_distance
	ORDER BY distance
	LIMIT :n
	"""
)


async def _notes(
	session: AsyncSession, user_id: uuid.UUID, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, Capture]:
	wanted = {i for i in ids if i is not None}
	if not wanted:
		return {}
	rows = await session.scalars(
		select(Capture).where(Capture.user_id == user_id, Capture.id.in_(wanted))
	)
	return {c.id: c for c in rows.all()}


async def _people(
	session: AsyncSession, user_id: uuid.UUID, ids: Iterable[uuid.UUID | None]
) -> dict[uuid.UUID, Person]:
	wanted = {i for i in ids if i is not None}
	if not wanted:
		return {}
	rows = await session.scalars(
		select(Person).where(Person.user_id == user_id, Person.id.in_(wanted))
	)
	return {p.id: p for p in rows.all()}


def _live(user_id: uuid.UUID) -> list[ColumnElement[bool]]:
	"""This user's rows that nobody has said no to."""
	return [Derivation.user_id == user_id, Derivation.status != "rejected"]


def _interleave(
	groups: Sequence[list[MapLink]], limit: int = MAX_LINKS
) -> list[MapLink]:
	"""Take from each group in turn, so a person with forty notes still shows their
	prayers and the people they're with, not forty notes."""
	out: list[MapLink] = []
	seen: set[tuple[str, str]] = set()
	for row in zip_longest(*groups):
		for link in row:
			if link is None or (link.node.kind, link.node.id) in seen:
				continue
			seen.add((link.node.kind, link.node.id))
			out.append(link)
			if len(out) == limit:
				return out
	return out


# --- one map per kind of centre ------------------------------------------------------


async def _person_map(
	session: AsyncSession, user: User, person_id: uuid.UUID, zone: ZoneInfo
) -> MapOut:
	person = await session.scalar(
		select(Person).where(Person.id == person_id, Person.user_id == user.id)
	)
	if person is None:
		raise NotFound
	mentions = (
		await session.scalars(
			select(Derivation)
			.where(*_live(user.id))
			.where(
				Derivation.kind == MENTION,
				Derivation.person_id == person.id,
				Derivation.capture_id.is_not(None),
			)
		)
	).all()
	notes = await _notes(session, user.id, (m.capture_id for m in mentions))
	shown = [
		c
		for c in sorted(notes.values(), key=lambda c: c.created_at, reverse=True)
		if not (faith_hidden(user) and c.space == "faith")
	]
	prayers = (
		await session.scalars(
			select(Derivation)
			.where(*_live(user.id))
			.where(Derivation.kind == PRAYER, Derivation.person_id == person.id)
			.order_by(Derivation.created_at.desc())
			.limit(4)
		)
	).all()
	# Who else those notes are about: the people this person comes up with.
	others = (
		await session.scalars(
			select(Derivation)
			.where(*_live(user.id))
			.where(
				Derivation.kind == MENTION,
				Derivation.capture_id.in_([c.id for c in shown] or [uuid.uuid4()]),
				Derivation.person_id.is_not(None),
				Derivation.person_id != person.id,
			)
		)
	).all()
	counts = Counter(o.person_id for o in others)
	with_them = await _people(session, user.id, counts)
	return MapOut(
		center=person_node(person),
		links=_interleave(
			[
				[MapLink(node=note_node(c, zone), relation="note") for c in shown[:8]],
				[
					MapLink(node=item_node(p, zone), relation="prayer for")
					for p in prayers
				],
				[
					MapLink(node=person_node(with_them[pid]), relation="with")
					for pid, _ in counts.most_common(4)
					if pid in with_them
				],
			]
		),
	)


async def _note_map(
	session: AsyncSession,
	user: User,
	capture_id: uuid.UUID,
	zone: ZoneInfo,
	max_distance: float,
) -> MapOut:
	capture = await session.scalar(
		select(Capture).where(Capture.id == capture_id, Capture.user_id == user.id)
	)
	if capture is None:
		raise NotFound
	found = (
		await session.scalars(
			select(Derivation)
			.where(*_live(user.id))
			.where(Derivation.capture_id == capture.id)
			.order_by(Derivation.created_at)
		)
	).all()
	people = await _people(
		session, user.id, (d.person_id for d in found if d.kind == MENTION)
	)
	updates = [d for d in found if d.kind == UPDATE and d.status == "confirmed"]
	targets = {
		t.id: t
		for t in (
			await session.scalars(
				select(Derivation)
				.where(*_live(user.id))
				.where(
					Derivation.id.in_([u.target_id for u in updates] or [uuid.uuid4()])
				)
			)
		).all()
	}
	space = capture.space if faith_hidden(user) else None
	near = await related_notes(
		session, user.id, capture.id, space=space, limit=3, max_distance=max_distance
	)
	related = await _notes(session, user.id, (cid for cid, _ in near))
	return MapOut(
		center=note_node(capture, zone),
		links=_interleave(
			[
				[
					MapLink(node=person_node(people[pid]), relation="about")
					for pid in dict.fromkeys(
						d.person_id for d in found if d.kind == MENTION and d.person_id
					)
					if pid in people
				][:5],
				[
					MapLink(node=item_node(d, zone), relation="from it")
					for d in found
					if d.kind in ("todo", "event", "idea")
				][:6],
				[
					MapLink(node=item_node(d, zone), relation="prayer")
					for d in found
					if d.kind == PRAYER
				][:3],
				[
					MapLink(
						node=item_node(targets[u.target_id], zone),
						relation="answers"
						if u.payload.get("relation") == "answers"
						else "carries on",
					)
					for u in updates
					if u.target_id in targets
				][:3],
				[
					MapLink(node=note_node(related[cid], zone), relation="related")
					for cid, _ in near
					if cid in related
				],
				[
					MapLink(node=item_node(d, zone), relation="passage")
					for d in found
					if d.kind == PASSAGE
				][:3],
			]
		),
	)


async def _updates_to(
	session: AsyncSession, user_id: uuid.UUID, target_id: uuid.UUID, relation: str
) -> list[Derivation]:
	rows = await session.scalars(
		select(Derivation)
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == UPDATE,
			Derivation.status == "confirmed",
			Derivation.target_id == target_id,
			Derivation.capture_id.is_not(None),
		)
		.order_by(Derivation.created_at)
	)
	return [u for u in rows.all() if u.payload.get("relation") == relation]


async def _idea_map(
	session: AsyncSession,
	user: User,
	idea_id: uuid.UUID,
	zone: ZoneInfo,
	max_distance: float,
) -> MapOut:
	idea = await session.scalar(
		select(Derivation)
		.where(*_live(user.id))
		.where(Derivation.id == idea_id, Derivation.kind == "idea")
	)
	if idea is None:
		raise NotFound
	thread = await _updates_to(session, user.id, idea.id, "continues")
	notes = await _notes(
		session, user.id, [idea.capture_id, *(u.capture_id for u in thread)]
	)
	mentions = (
		await session.scalars(
			select(Derivation)
			.where(*_live(user.id))
			.where(
				Derivation.kind == MENTION,
				Derivation.capture_id == idea.capture_id,
				Derivation.person_id.is_not(None),
			)
		)
		if idea.capture_id is not None
		else None
	)
	people = await _people(
		session, user.id, (m.person_id for m in (mentions.all() if mentions else []))
	)
	near = (
		await session.execute(
			_NEAR_IDEAS,
			{"uid": user.id, "idea_id": idea.id, "n": 4, "max_distance": max_distance},
		)
	).all()
	near_ideas = {
		d.id: d
		for d in (
			await session.scalars(
				select(Derivation)
				.where(*_live(user.id))
				.where(Derivation.id.in_([row[0] for row in near] or [uuid.uuid4()]))
			)
		).all()
	}
	source = notes.get(idea.capture_id) if idea.capture_id else None
	return MapOut(
		center=item_node(idea, zone),
		links=_interleave(
			[
				[MapLink(node=note_node(source, zone), relation="written in")]
				if source
				else [],
				[
					MapLink(
						node=note_node(notes[u.capture_id], zone),
						relation="carried on",
					)
					for u in thread
					if u.capture_id in notes
				],
				[
					MapLink(node=person_node(p), relation="about")
					for p in people.values()
				][:3],
				[
					MapLink(node=item_node(near_ideas[row[0]], zone), relation="near")
					for row in near
					if row[0] in near_ideas
				],
			]
		),
	)


async def _prayer_map(
	session: AsyncSession, user: User, prayer_id: uuid.UUID, zone: ZoneInfo
) -> MapOut:
	prayer = await session.scalar(
		select(Derivation)
		.where(*_live(user.id))
		.where(Derivation.id == prayer_id, Derivation.kind == PRAYER)
	)
	if prayer is None:
		raise NotFound
	answers = await _updates_to(session, user.id, prayer.id, "answers")
	notes = await _notes(
		session, user.id, [prayer.capture_id, *(u.capture_id for u in answers)]
	)
	person = (
		await session.scalar(
			select(Person).where(
				Person.id == prayer.person_id, Person.user_id == user.id
			)
		)
		if prayer.person_id
		else None
	)
	also = (
		(
			await session.scalars(
				select(Derivation)
				.where(*_live(user.id))
				.where(
					Derivation.kind == PRAYER,
					Derivation.person_id == prayer.person_id,
					Derivation.id != prayer.id,
				)
				.order_by(Derivation.created_at.desc())
				.limit(3)
			)
		).all()
		if prayer.person_id
		else []
	)
	source = notes.get(prayer.capture_id) if prayer.capture_id else None
	return MapOut(
		center=item_node(prayer, zone),
		links=_interleave(
			[
				[MapLink(node=person_node(person), relation="for")] if person else [],
				[MapLink(node=note_node(source, zone), relation="prayed in")]
				if source
				else [],
				[
					MapLink(
						node=note_node(notes[u.capture_id], zone),
						relation="answered in",
					)
					for u in answers
					if u.capture_id in notes
				],
				[MapLink(node=item_node(p, zone), relation="also for") for p in also],
			]
		),
	)


async def build_map(
	session: AsyncSession,
	user_id: uuid.UUID,
	kind: CenterKind,
	node_id: uuid.UUID,
	*,
	max_distance: float,
) -> MapOut:
	user = await session.get(User, user_id)
	if user is None:
		raise NotFound
	zone = tz_or_utc(user.timezone)
	if kind == "person":
		return await _person_map(session, user, node_id, zone)
	if kind == "note":
		return await _note_map(session, user, node_id, zone, max_distance)
	if kind == "idea":
		return await _idea_map(session, user, node_id, zone, max_distance)
	return await _prayer_map(session, user, node_id, zone)
