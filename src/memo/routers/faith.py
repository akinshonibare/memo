"""Faith (ADR 0042): the Faith screen - Within, in the app since ADR 0058 - prayers,
the reading plan, and how you've sounded (mood).

Prayers are derivations of kind 'prayer', but they live here rather than under
/v1/derivations: the generic routes serve the Planner, the phone sync and apps built
before Faith, none of which should ever meet one.
"""

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import ValidationError
from sqlalchemy import select

from memo.auth import CurrentUser
from memo.db import DbSession, commit_and_rebind
from memo.ids import uuid7
from memo.models import Derivation, Person, User
from memo.schemas.derivations import (
	PRAYER,
	READING,
	UNREVIEWED,
	PrayerPayload,
	validation_detail,
)
from memo.schemas.faith import (
	FaithOut,
	LogReading,
	PrayerCreate,
	PrayerOut,
	PrayerPatch,
	ReadingState,
	SetReading,
)
from memo.schemas.mood import MoodDayDetail, SetMood
from memo.services.faith import (
	ANSWERED_SHOWN,
	NotAPassage,
	live_prayers,
	log_reading,
	moment,
	names_for,
	passage_index,
	prayer_out,
	prayers_out,
	reading_state,
	set_reading,
)
from memo.services.mood import (
	MAX_DAYS,
	clear_day,
	day_detail,
	mood_on,
	mood_week,
	set_day,
	today_for,
)
from memo.services.morning import tz_or_utc

router = APIRouter(prefix="/faith", tags=["faith"])


async def _user(session: DbSession, user_id: uuid.UUID) -> User:
	user = await session.get(User, user_id)
	if user is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
	return user


async def _own_person(
	session: DbSession, user_id: uuid.UUID, person_id: uuid.UUID
) -> None:
	found = await session.scalar(
		select(Person.id).where(Person.id == person_id, Person.user_id == user_id)
	)
	if found is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "person not found")


def _payload(values: dict[str, object]) -> dict[str, object]:
	try:
		return PrayerPayload.model_validate(values).model_dump(mode="json")
	except ValidationError as exc:
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, validation_detail(exc)
		) from exc


@router.get("", response_model=FaithOut)
async def faith(session: DbSession, user_id: CurrentUser) -> FaithOut:
	"""The Faith screen: the one thing to remember today, what you're reading, the
	passages your notes talk about, open prayers (newest first), and recently
	answered ones."""
	user = await _user(session, user_id)
	open_rows = (
		await session.scalars(
			live_prayers(user_id)
			.where(Derivation.completed_at.is_(None))
			.order_by(Derivation.created_at.desc(), Derivation.id.desc())
		)
	).all()
	answered_rows = (
		await session.scalars(
			live_prayers(user_id)
			.where(Derivation.completed_at.is_not(None))
			.order_by(Derivation.completed_at.desc(), Derivation.id.desc())
			.limit(ANSWERED_SHOWN)
		)
	).all()
	return FaithOut(
		enabled=user.faith_enabled,
		translation=user.bible_translation,
		moment=await moment(session, user),
		reading=await reading_state(session, user),
		passages=await passage_index(session, user),
		open=await prayers_out(session, user_id, open_rows),
		answered=await prayers_out(session, user_id, answered_rows),
		mood=await mood_week(session, user) if mood_on(user) else None,
	)


@router.post("/prayers", status_code=status.HTTP_201_CREATED, response_model=PrayerOut)
async def create_prayer(
	body: PrayerCreate, session: DbSession, user_id: CurrentUser
) -> PrayerOut:
	if body.person_id is not None:
		await _own_person(session, user_id, body.person_id)
	prayer = Derivation(
		id=uuid7(),
		user_id=user_id,
		capture_id=None,
		kind=PRAYER,
		payload=_payload({"title": body.title, "notes": body.notes}),
		status="user_created",
		extractor_version="user",
		confidence=None,
		reviewed_at=dt.datetime.now(dt.UTC),
		person_id=body.person_id,
	)
	session.add(prayer)
	await commit_and_rebind(session, user_id)
	names = await names_for(session, user_id, [prayer.person_id])
	return prayer_out(prayer, names)


@router.patch("/prayers/{prayer_id}", response_model=PrayerOut)
async def update_prayer(
	prayer_id: uuid.UUID, body: PrayerPatch, session: DbSession, user_id: CurrentUser
) -> PrayerOut:
	prayer = await session.scalar(
		select(Derivation).where(
			Derivation.id == prayer_id,
			Derivation.user_id == user_id,
			Derivation.kind == PRAYER,
		)
	)
	if prayer is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	fields = body.model_fields_set
	values = dict(prayer.payload)
	for key in ("title", "notes", "answer"):
		if key in fields:
			values[key] = getattr(body, key)
	if "answered" in fields and body.answered is False:
		values["answer"] = None  # no longer answered: how it was no longer applies
	prayer.payload = _payload(values)
	if "person_id" in fields:
		if body.person_id is not None:
			await _own_person(session, user_id, body.person_id)
		prayer.person_id = body.person_id
	if "answered" in fields and body.answered is not None:
		if body.answered and prayer.completed_at is None:
			prayer.completed_at = dt.datetime.now(dt.UTC)
		elif not body.answered:
			prayer.completed_at = None
	if body.removed:
		prayer.status = "rejected"
	elif prayer.status in UNREVIEWED:
		# Acting on a prayer memo found says it was real, as for any item; and a
		# re-read of its note then leaves it alone.
		prayer.status = "confirmed"
	prayer.reviewed_at = dt.datetime.now(dt.UTC)
	await commit_and_rebind(session, user_id)
	names = await names_for(session, user_id, [prayer.person_id])
	return prayer_out(prayer, names)


def _not_a_passage(exc: NotAPassage) -> HTTPException:
	return HTTPException(
		status.HTTP_422_UNPROCESSABLE_CONTENT,
		f"memo couldn't find a Bible passage in {str(exc)!r}",
	)


@router.put("/reading", response_model=ReadingState)
async def choose_reading(
	body: SetReading, session: DbSession, user_id: CurrentUser
) -> ReadingState:
	"""Set what you're reading: a book ("John"), a chapter or a few ("Romans 8"), or
	some verses. Null clears it. Nothing you read before is touched."""
	user = await _user(session, user_id)
	try:
		set_reading(user, body.ref)
	except NotAPassage as exc:
		raise _not_a_passage(exc) from exc
	await commit_and_rebind(session, user_id)
	return await reading_state(session, await _user(session, user_id))


@router.post(
	"/readings", status_code=status.HTTP_201_CREATED, response_model=ReadingState
)
async def read(
	body: LogReading, session: DbSession, user_id: CurrentUser
) -> ReadingState:
	"""You read something: what you typed, or else the next chapter of what you're
	reading. Once per passage per day."""
	user = await _user(session, user_id)
	today = dt.datetime.now(tz_or_utc(user.timezone)).date()
	on = body.on or today
	if on > today:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a future day")
	try:
		await log_reading(session, user, ref=body.ref, on=on)
	except NotAPassage as exc:
		raise _not_a_passage(exc) from exc
	await commit_and_rebind(session, user_id)
	return await reading_state(session, await _user(session, user_id))


@router.delete("/readings/{reading_id}", response_model=ReadingState)
async def unread(
	reading_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> ReadingState:
	"""Undo a reading. Gone, not rejected: no model found it, so there is no label to
	keep."""
	reading = await session.scalar(
		select(Derivation).where(
			Derivation.id == reading_id,
			Derivation.user_id == user_id,
			Derivation.kind == READING,
		)
	)
	if reading is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	await session.delete(reading)
	await commit_and_rebind(session, user_id)
	return await reading_state(session, await _user(session, user_id))


# --- mood (ADR 0058) ------------------------------------------------------------------


def _mood_day(user: User, day: dt.date) -> dt.date:
	today = today_for(user)
	if day > today:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a future day")
	if day < today - dt.timedelta(days=MAX_DAYS):
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, f"only the last {MAX_DAYS} days"
		)
	return day


@router.get("/mood/{day}", response_model=MoodDayDetail)
async def mood_of_day(
	day: dt.date, session: DbSession, user_id: CurrentUser
) -> MoodDayDetail:
	"""One day: how it reads, from which notes and the phrase in each, and your own
	word for it if you gave one."""
	user = await _user(session, user_id)
	return await day_detail(session, user, _mood_day(user, day))


@router.put("/mood/{day}", response_model=MoodDayDetail)
async def say_mood(
	day: dt.date, body: SetMood, session: DbSession, user_id: CurrentUser
) -> MoodDayDetail:
	"""Your word for a day. It outranks every read of the day's notes; saying again
	replaces it."""
	user = await _user(session, user_id)
	on = _mood_day(user, day)
	await set_day(session, user_id, on, body.valence)
	await commit_and_rebind(session, user_id)
	return await day_detail(session, await _user(session, user_id), on)


@router.delete("/mood/{day}", response_model=MoodDayDetail)
async def unsay_mood(
	day: dt.date, session: DbSession, user_id: CurrentUser
) -> MoodDayDetail:
	"""Take your word back: the day reads from the notes again."""
	user = await _user(session, user_id)
	on = _mood_day(user, day)
	await clear_day(session, user_id, on)
	await commit_and_rebind(session, user_id)
	return await day_detail(session, await _user(session, user_id), on)
