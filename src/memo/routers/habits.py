"""Habits (ADR 0043): the Habits screen, a habit's days, suggestions, and the tap that
logs a day or takes one away. Counts are computed on every read (services/habits)."""

import datetime as dt
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select, update

from memo.auth import CurrentUser
from memo.db import DbSession, commit_and_rebind
from memo.ids import uuid7
from memo.models import Derivation, Habit, User
from memo.schemas.derivations import ACTIVITY
from memo.schemas.habits import (
	Dismiss,
	HabitCreate,
	HabitDetail,
	HabitOut,
	HabitPatch,
	HabitsOut,
	LogDay,
	normalize_key,
)
from memo.services.habits import (
	habit_detail,
	habit_out,
	key_for,
	load_sources,
	name_for,
	source_for,
	suggestion,
	today_for,
	tracked,
)

router = APIRouter(prefix="/habits", tags=["habits"])


async def _user(session: DbSession, user_id: uuid.UUID) -> User:
	user = await session.get(User, user_id)
	if user is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "user not found")
	return user


async def _habit(session: DbSession, user_id: uuid.UUID, habit_id: uuid.UUID) -> Habit:
	habit = await session.scalar(
		select(Habit).where(
			Habit.id == habit_id, Habit.user_id == user_id, Habit.status == "active"
		)
	)
	if habit is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	return habit


async def _out(session: DbSession, user: User, habit: Habit) -> HabitOut:
	sources = await load_sources(session, user)
	return habit_out(habit, sources.days_for(habit.key), today_for(user))


@router.get("", response_model=HabitsOut)
async def list_habits(session: DbSession, user_id: CurrentUser) -> HabitsOut:
	"""Every habit you track, with its rhythm, and the one habit memo would suggest."""
	user = await _user(session, user_id)
	habits = await tracked(session, user_id)
	sources = await load_sources(session, user)
	today = today_for(user)
	return HabitsOut(
		enabled=user.habits_enabled,
		habits=[
			habit_out(h, sources.days_for(h.key), today)
			for h in habits
			if h.status == "active"
		],
		suggestion=suggestion(habits, sources, today) if user.habits_enabled else None,
	)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=HabitOut)
async def track(
	body: HabitCreate, session: DbSession, user_id: CurrentUser
) -> HabitOut:
	"""Track a habit: a suggestion accepted (with its key), or one you name. A name
	memo recognises is filed under its own key, so "Exercise" counts the gym notes it
	already has. One you dismissed or stopped comes back, with its history."""
	user = await _user(session, user_id)
	key = normalize_key(body.key) if body.key else key_for(body.name)
	if not key:
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, "a habit needs letters in its name"
		)
	habit = await session.scalar(
		select(Habit).where(Habit.user_id == user_id, Habit.key == key)
	)
	if habit is None:
		habit = Habit(
			id=uuid7(),
			user_id=user_id,
			key=key,
			name=body.name or name_for(key),
			source=source_for(key),
			target_per_week=body.target_per_week,
		)
		session.add(habit)
	else:
		habit.status = "active"
		habit.name = body.name
		if body.target_per_week is not None:
			habit.target_per_week = body.target_per_week
	await commit_and_rebind(session, user_id)
	return await _out(session, user, habit)


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
async def dismiss(body: Dismiss, session: DbSession, user_id: CurrentUser) -> None:
	"""'Not now' on a suggestion: memo won't suggest this one again."""
	key = normalize_key(body.key)
	existing = await session.scalar(
		select(Habit).where(Habit.user_id == user_id, Habit.key == key)
	)
	if existing is None:
		session.add(
			Habit(
				id=uuid7(),
				user_id=user_id,
				key=key,
				name=name_for(key),
				source=source_for(key),
				status="dismissed",
			)
		)
		await commit_and_rebind(session, user_id)


@router.get("/{habit_id}", response_model=HabitDetail)
async def get_habit(
	habit_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> HabitDetail:
	"""A habit's rhythm and its last 60 days, each with what made it count."""
	user = await _user(session, user_id)
	habit = await _habit(session, user_id, habit_id)
	sources = await load_sources(session, user)
	return habit_detail(habit, sources.days_for(habit.key), today_for(user))


@router.patch("/{habit_id}", response_model=HabitOut)
async def update_habit(
	habit_id: uuid.UUID, body: HabitPatch, session: DbSession, user_id: CurrentUser
) -> HabitOut:
	user = await _user(session, user_id)
	habit = await _habit(session, user_id, habit_id)
	if "name" in body.model_fields_set and body.name:
		habit.name = " ".join(body.name.split())
	if "target_per_week" in body.model_fields_set:
		habit.target_per_week = body.target_per_week  # null: no target
	await commit_and_rebind(session, user_id)
	return await _out(session, user, habit)


@router.delete("/{habit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def stop_tracking(
	habit_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> None:
	"""Stop tracking: the habit goes (and isn't suggested again); what you did stays,
	so tracking it again brings its history back."""
	habit = await _habit(session, user_id, habit_id)
	habit.status = "dismissed"
	await commit_and_rebind(session, user_id)


@router.post("/{habit_id}/log", response_model=HabitOut)
async def log_day(
	habit_id: uuid.UUID, body: LogDay, session: DbSession, user_id: CurrentUser
) -> HabitOut:
	"""A day you did it but didn't write about. Once a day: a second tap is a no-op."""
	user = await _user(session, user_id)
	habit = await _habit(session, user_id, habit_id)
	today = today_for(user)
	day = body.date or today
	if day > today:
		raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "not a future day")
	existing = await session.scalar(
		select(Derivation.id).where(
			Derivation.user_id == user_id,
			Derivation.kind == ACTIVITY,
			Derivation.capture_id.is_(None),
			Derivation.status != "rejected",
			Derivation.payload["key"].astext == habit.key,
			Derivation.payload["occurred_on"].astext == day.isoformat(),
		)
	)
	if existing is None:
		session.add(
			Derivation(
				id=uuid7(),
				user_id=user_id,
				capture_id=None,
				kind=ACTIVITY,
				payload={"key": habit.key, "label": "Logged", "occurred_on": str(day)},
				status="user_created",
				extractor_version="user",
				confidence=None,
				reviewed_at=dt.datetime.now(dt.UTC),
			)
		)
		await commit_and_rebind(session, user_id)
	return await _out(session, user, habit)


@router.delete("/{habit_id}/days/{day}", response_model=HabitOut)
async def remove_day(
	habit_id: uuid.UUID, day: dt.date, session: DbSession, user_id: CurrentUser
) -> HabitOut:
	"""That day doesn't count: every activity for this habit on it is rejected - kept,
	so a re-read of the note doesn't bring it back, and as the eval's precision label.
	A contact (People) or a reading (Faith) is changed where it lives."""
	user = await _user(session, user_id)
	habit = await _habit(session, user_id, habit_id)
	await session.execute(
		update(Derivation)
		.where(
			Derivation.user_id == user_id,
			Derivation.kind == ACTIVITY,
			Derivation.status != "rejected",
			Derivation.payload["key"].astext == habit.key,
			Derivation.payload["occurred_on"].astext == day.isoformat(),
		)
		.values(status="rejected", reviewed_at=dt.datetime.now(dt.UTC))
	)
	await commit_and_rebind(session, user_id)
	return await _out(session, user, habit)
