"""People (ADR 0029): who your notes are about, when you were last in touch, and the
"which one?" question when a name could be more than one person.

Every route that changes who a mention points at takes the same per-user lock as the
people stage, so a merge and a note being read at the same moment cannot interleave.
"""

import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select, update

from memo.auth import CurrentUser
from memo.db import DbSession, commit_and_rebind
from memo.ids import uuid7
from memo.models import Capture, Chunk, Derivation, Person, User
from memo.routers.captures import get_queue
from memo.schemas.derivations import MENTION, UNREVIEWED, DerivationOut
from memo.schemas.faith import PrayerOut
from memo.schemas.people import (
	ContactBody,
	MentionOut,
	MentionPatch,
	MentionPayload,
	MergeBody,
	PeopleList,
	PersonDetail,
	PersonOut,
	PersonPatch,
)
from memo.services.derivation_index import enqueue_reembed
from memo.services.faith import faith_hidden, live_prayers, prayer_out
from memo.services.habits import is_family
from memo.services.morning import tz_or_utc
from memo.services.people import (
	collapse_duplicates,
	count_unread_notes,
	count_unreadable_notes,
	is_due,
	last_contacts,
	load_people,
	lock_people,
	mention_counts,
	mention_out,
	new_person,
	normalize,
	owned_person,
	prune_if_empty,
	timeline,
	with_alias,
)
from memo.services.queue import Queue

router = APIRouter(prefix="/people", tags=["people"])
mentions_router = APIRouter(prefix="/mentions", tags=["people"])


async def _today(session: DbSession, user_id: uuid.UUID) -> tuple[dt.date, str]:
	user = await session.get(User, user_id)
	timezone = user.timezone if user is not None else "UTC"
	return dt.datetime.now(tz_or_utc(timezone)).date(), timezone


def _person_out(
	p: Person,
	lasts: dict[uuid.UUID, dt.date],
	counts: dict[uuid.UUID, int],
	today: dt.date,
) -> PersonOut:
	return PersonOut(
		id=p.id,
		name=p.name,
		aliases=list(p.aliases or []),
		note=p.note,
		reach_out_every_days=p.reach_out_every_days,
		snoozed_until=p.snoozed_until,
		last_contact_on=lasts.get(p.id),
		mention_count=counts.get(p.id, 0),
		due=is_due(p, lasts.get(p.id), today),
		created_at=p.created_at,
		family=is_family(p),
	)


async def _person_or_404(
	session: DbSession, user_id: uuid.UUID, person_id: uuid.UUID
) -> Person:
	person = await owned_person(session, user_id, person_id)
	if person is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")
	return person


@router.get("", response_model=PeopleList)
async def list_people(session: DbSession, user_id: CurrentUser) -> PeopleList:
	today, _ = await _today(session, user_id)
	people = await load_people(session, user_id)
	lasts = await last_contacts(session, user_id)
	counts = await mention_counts(session, user_id)
	unresolved = await session.scalar(
		select(func.count())
		.select_from(Derivation)
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == MENTION,
			Derivation.status == "pending_review",
			Derivation.person_id.is_(None),
		)
	)
	return PeopleList(
		people=[_person_out(p, lasts, counts, today) for p in people],
		unresolved=int(unresolved or 0),
		unread_notes=await count_unread_notes(session, user_id),
		unreadable_notes=await count_unreadable_notes(session, user_id),
	)


@router.get("/unresolved", response_model=list[MentionOut])
async def unresolved_mentions(
	session: DbSession, user_id: CurrentUser
) -> list[MentionOut]:
	"""Who's who: every mention waiting for "which one?", newest note first, each with
	the people it could be."""
	people = {p.id: p for p in await load_people(session, user_id)}
	lasts = await last_contacts(session, user_id)
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
			Derivation.status == "pending_review",
			Derivation.person_id.is_(None),
		)
		.order_by(
			func.coalesce(Capture.created_at, Derivation.created_at).desc(),
			Derivation.id,
		)
	)
	return [
		mention_out(
			d,
			people=people,
			lasts=lasts,
			written_at=written_at,
			excerpt=" ".join(body.split())[:200] if body else None,
		)
		for d, written_at, body in rows.all()
	]


async def _detail(
	session: DbSession, user_id: uuid.UUID, person: Person
) -> PersonDetail:
	today, timezone = await _today(session, user_id)
	zone = tz_or_utc(timezone)
	people = {p.id: p for p in await load_people(session, user_id)}
	lasts = await last_contacts(session, user_id)
	counts = await mention_counts(session, user_id)
	entries = (await timeline(session, user_id, [person.id], zone)).get(person.id, [])
	notes = [t.mention.capture_id for t in entries if t.mention.capture_id]
	open_todos: list[Derivation] = []
	upcoming: list[Derivation] = []
	if notes:
		items = (
			await session.scalars(
				select(Derivation)
				.where(
					Derivation.user_id == user_id,
					Derivation.capture_id.in_(notes),
					Derivation.kind.in_(("todo", "event")),
					Derivation.status != "rejected",
					Derivation.completed_at.is_(None),
				)
				.order_by(Derivation.created_at, Derivation.id)
			)
		).all()
		now = dt.datetime.now(dt.UTC)
		open_todos = [d for d in items if d.kind == "todo"]
		upcoming = sorted(
			(
				d
				for d in items
				if d.kind == "event"
				and d.payload.get("starts_at")
				and dt.datetime.fromisoformat(d.payload["starts_at"]) >= now
			),
			key=lambda d: dt.datetime.fromisoformat(d.payload["starts_at"]),
		)
	return PersonDetail(
		**_person_out(person, lasts, counts, today).model_dump(),
		mentions=[
			mention_out(
				t.mention,
				people=people,
				lasts=lasts,
				written_at=t.written_at,
				excerpt=t.excerpt,
			)
			for t in entries
		],
		open_todos=[DerivationOut.model_validate(d) for d in open_todos],
		upcoming_events=[DerivationOut.model_validate(d) for d in upcoming],
		prayers=await _prayers_for(session, user_id, person),
	)


async def _prayers_for(
	session: DbSession, user_id: uuid.UUID, person: Person
) -> list[PrayerOut]:
	"""Prayers for this person (ADR 0042), only while Faith is on."""
	if not faith_hidden(await session.get(User, user_id)):
		return []
	rows = (
		await session.scalars(
			live_prayers(user_id)
			.where(Derivation.person_id == person.id)
			.order_by(Derivation.created_at.desc(), Derivation.id.desc())
		)
	).all()
	return [prayer_out(d, {person.id: person.name}) for d in rows]


@router.get("/{person_id}", response_model=PersonDetail)
async def get_person(
	person_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> PersonDetail:
	"""A person: when you were last in touch, every note about them, and the open
	todos and coming events in those notes."""
	person = await _person_or_404(session, user_id, person_id)
	return await _detail(session, user_id, person)


@router.patch("/{person_id}", response_model=PersonOut)
async def update_person(
	person_id: uuid.UUID, body: PersonPatch, session: DbSession, user_id: CurrentUser
) -> PersonOut:
	await lock_people(session, user_id)
	person = await _person_or_404(session, user_id, person_id)
	fields = body.model_fields_set
	if "aliases" in fields:
		person.aliases = list(body.aliases or [])
	if "name" in fields and body.name is not None and body.name != person.name:
		# Renaming never loses a name memo matches on: "Mum" renamed to "Folake"
		# keeps "Mum" as an alias, so the next note that says "Mum" still links.
		previous = person.name
		person.name = body.name
		person.aliases = with_alias(person, previous)
	own = normalize(person.name)
	person.aliases = [a for a in person.aliases if normalize(a) != own]
	if "note" in fields:
		person.note = body.note
	if "reach_out_every_days" in fields:
		person.reach_out_every_days = body.reach_out_every_days
	if "snoozed_until" in fields:
		person.snoozed_until = body.snoozed_until
	if "family" in fields:
		person.family = body.family
	await session.flush()
	today, _ = await _today(session, user_id)
	return _person_out(
		person,
		await last_contacts(session, user_id),
		await mention_counts(session, user_id),
		today,
	)


@router.post(
	"/{person_id}/contact",
	status_code=status.HTTP_201_CREATED,
	response_model=PersonOut,
)
async def log_contact(
	person_id: uuid.UUID,
	body: ContactBody,
	session: DbSession,
	user_id: CurrentUser,
	queue: Annotated[Queue, Depends(get_queue)],
) -> PersonOut:
	"""'Spoke to them': memo only knows what it is told, and iOS gives no app your
	calls or messages, so this is the one-tap way to say so. A mention you added,
	with no note behind it, like an item added straight into the Planner."""
	# The same lock as a merge: a contact logged against a person being merged away
	# would otherwise hit the foreign key as a 500.
	await lock_people(session, user_id)
	person = await _person_or_404(session, user_id, person_id)
	today, _ = await _today(session, user_id)
	occurred_on = body.occurred_on or today
	if occurred_on > today:
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, "contact can't be in the future"
		)
	payload = MentionPayload(
		name=person.name, contact=body.contact, occurred_on=occurred_on
	).model_dump(mode="json", exclude={"confidence"})
	mention = Derivation(
		id=uuid7(),
		user_id=user_id,
		capture_id=None,
		kind=MENTION,
		payload=payload,
		status="user_created",
		extractor_version="user",
		confidence=None,
		reviewed_at=dt.datetime.now(dt.UTC),
		person_id=person.id,
	)
	session.add(mention)
	person.snoozed_until = None
	await commit_and_rebind(session, user_id)
	await enqueue_reembed(queue, mention)
	return _person_out(
		person,
		await last_contacts(session, user_id),
		await mention_counts(session, user_id),
		today,
	)


@router.post("/{person_id}/merge", response_model=PersonDetail)
async def merge_person(
	person_id: uuid.UUID, body: MergeBody, session: DbSession, user_id: CurrentUser
) -> PersonDetail:
	"""These are one person: every mention of `person_id` moves to `into`, its names
	become aliases there, and it is deleted. The fix for a split - the error the
	resolver is tuned to make instead of a wrong merge."""
	if body.into == person_id:
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, "a person can't merge into themself"
		)
	await lock_people(session, user_id)
	source = await _person_or_404(session, user_id, person_id)
	target = await _person_or_404(session, user_id, body.into)
	await session.execute(
		update(Derivation)
		.where(Derivation.user_id == user_id, Derivation.person_id == source.id)
		.values(person_id=target.id)
	)
	for name in (source.name, *(source.aliases or [])):
		target.aliases = with_alias(target, name)
	target.note = target.note or source.note
	await session.delete(source)
	await session.flush()
	# A note that named both is now two mentions of one person.
	await collapse_duplicates(session, user_id, target.id)
	return await _detail(session, user_id, target)


@mentions_router.patch("/{mention_id}", response_model=MentionOut)
async def resolve_mention(
	mention_id: uuid.UUID, body: MentionPatch, session: DbSession, user_id: CurrentUser
) -> MentionOut:
	"""Answer "who is this?": a person you know, someone new, or not a person at all
	(reject). Linking a name that memo wouldn't have matched ("Mum", a misheard
	spelling) teaches it that name, so the next note links on its own."""
	await lock_people(session, user_id)
	mention = await session.scalar(
		select(Derivation).where(
			Derivation.id == mention_id,
			Derivation.user_id == user_id,
			Derivation.kind == MENTION,
		)
	)
	if mention is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "mention not found")
	payload = MentionPayload.model_validate(mention.payload)
	previous = mention.person_id
	now = dt.datetime.now(dt.UTC)

	if body.person_id is not None or body.new_person:
		if body.person_id is not None:
			person = await _person_or_404(session, user_id, body.person_id)
			for name in (payload.name, payload.full_name):
				if name:
					person.aliases = with_alias(person, name)
		else:
			person = new_person(user_id, payload)
			session.add(person)
			await session.flush()
		mention.person_id = person.id
		if mention.status != "user_created":
			mention.status = "confirmed"
	elif body.status == "confirmed":
		if mention.person_id is None:
			raise HTTPException(
				status.HTTP_422_UNPROCESSABLE_CONTENT,
				"say who this is first (person_id or new_person)",
			)
		if mention.status in UNREVIEWED:
			mention.status = "confirmed"
	else:
		mention.status = "rejected"
		# Like any rejected derivation, it stops answering questions (ADR 0021).
		await session.execute(
			update(Chunk)
			.where(
				Chunk.derivation_id == mention.id,
				Chunk.user_id == user_id,
				Chunk.superseded_at.is_(None),
			)
			.values(superseded_at=now)
		)
	mention.reviewed_at = now
	await session.flush()
	# A person left with no live mention was a misreading; don't keep the phantom.
	stale = []
	if previous is not None and previous != mention.person_id:
		stale.append(previous)
	if mention.status == "rejected" and mention.person_id is not None:
		stale.append(mention.person_id)
	await prune_if_empty(session, user_id, stale)
	if mention.person_id is not None and mention.status != "rejected":
		# Linking to someone this note already names: keep one mention of them.
		gone = await collapse_duplicates(session, user_id, mention.person_id)
		if mention.id in gone:
			survivor = await session.scalar(
				select(Derivation).where(
					Derivation.user_id == user_id,
					Derivation.capture_id == mention.capture_id,
					Derivation.person_id == mention.person_id,
					Derivation.kind == MENTION,
					Derivation.status != "rejected",
				)
			)
			assert survivor is not None
			mention = survivor
	await session.refresh(mention)

	people = {p.id: p for p in await load_people(session, user_id)}
	lasts = await last_contacts(session, user_id)
	return mention_out(mention, people=people, lasts=lasts)
