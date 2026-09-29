"""Faith (ADR 0042): the Faith screen, prayers, and the reading plan.

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
from memo.schemas.derivations import PRAYER, PrayerPayload, validation_detail
from memo.schemas.faith import FaithOut, PlanOut, PrayerCreate, PrayerOut, PrayerPatch
from memo.services.faith import (
	ANSWERED_SHOWN,
	live_prayers,
	mark_read,
	moment,
	names_for,
	plan_state,
	prayer_out,
	prayers_out,
	undo_read,
)

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
	"""The Faith screen: where the plan is, the one thing to remember today, open
	prayers (newest first), and recently answered ones."""
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
		plan=await plan_state(session, user_id),
		moment=await moment(session, user),
		open=await prayers_out(session, user_id, open_rows),
		answered=await prayers_out(session, user_id, answered_rows),
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
	prayer.reviewed_at = dt.datetime.now(dt.UTC)
	await commit_and_rebind(session, user_id)
	names = await names_for(session, user_id, [prayer.person_id])
	return prayer_out(prayer, names)


@router.post("/readings", response_model=PlanOut)
async def read_next(session: DbSession, user_id: CurrentUser) -> PlanOut:
	"""Mark the next reading read. The plan moves on by one; it never skips ahead to
	catch up with a calendar."""
	state = await mark_read(session, user_id)
	await commit_and_rebind(session, user_id)
	return state


@router.delete("/readings/last", response_model=PlanOut)
async def undo_last(session: DbSession, user_id: CurrentUser) -> PlanOut:
	state = await undo_read(session, user_id)
	await commit_and_rebind(session, user_id)
	return state
