"""Apple Health (ADR 0050): workouts the phone read from Health, filed as activities.

The one place memo reads from the phone rather than writing to it (ADR 0034's rule,
with Akin's exception). Each workout becomes an ordinary `activity` with no note, so it
counts a habit's day, carries its distance and time into milestones, and can be removed
from a day like anything else. What memo doesn't count as a habit (golf, sailing, a
cooldown) is left in Health.

A sync sends a whole window, so it is a replacement, not a queue: filed if new, updated
if Health changed it, and let go if Health no longer has it. A day you removed in memo
stays removed: its rejected rows are kept, and a sync never brings them back.
"""

import datetime as dt
import uuid
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import DateTime, delete, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from memo.ids import uuid7
from memo.models import HEALTH_WORKOUT_WHERE, Derivation, User
from memo.schemas.derivations import ACTIVITY
from memo.schemas.habits import LIMITS, stored_amount
from memo.schemas.health import HealthSync, HealthSyncOut, WorkoutIn
from memo.services.habits import HEALTH, tz_or_utc

log = structlog.get_logger("memo.health")

VERSION = "health-v1"
_LOCK_NAMESPACE = 50  # ADR 0050: one user's syncs run one at a time

# HKWorkoutActivityType's raw values, as a habit key and the words for the workout.
# Listed, not guessed: a type that isn't here isn't a habit memo counts.
TYPES: dict[int, tuple[str, str]] = {
	37: ("running", "Run"),
	71: ("running", "Run"),  # wheelchair run pace
	52: ("walking", "Walk"),
	70: ("walking", "Walk"),  # wheelchair walk pace
	24: ("walking", "Hike"),
	13: ("cycling", "Ride"),
	74: ("cycling", "Hand cycle"),
	46: ("swimming", "Swim"),
	57: ("yoga", "Yoga"),
	66: ("yoga", "Pilates"),
	50: ("gym", "Strength training"),
	20: ("gym", "Functional strength"),
	63: ("gym", "HIIT"),
	11: ("gym", "Cross training"),
	59: ("gym", "Core training"),
	16: ("gym", "Elliptical"),
	35: ("gym", "Rowing"),
	44: ("gym", "Stair climbing"),
	68: ("gym", "Stairs"),
	73: ("gym", "Mixed cardio"),
	64: ("gym", "Jump rope"),
	65: ("gym", "Kickboxing"),
	8: ("gym", "Boxing"),
	69: ("gym", "Step training"),
	58: ("gym", "Barre"),
	41: ("sport", "Football"),
	6: ("sport", "Basketball"),
	48: ("sport", "Tennis"),
	9: ("sport", "Climbing"),
	4: ("sport", "Badminton"),
	43: ("sport", "Squash"),
	47: ("sport", "Table tennis"),
	51: ("sport", "Volleyball"),
	36: ("sport", "Rugby"),
	1: ("sport", "American football"),
	10: ("sport", "Cricket"),
	25: ("sport", "Hockey"),
	79: ("sport", "Pickleball"),
	28: ("sport", "Martial arts"),
	34: ("sport", "Racquetball"),
	23: ("sport", "Handball"),
	5: ("sport", "Baseball"),
	42: ("sport", "Softball"),
}
# A distance means something for these; an elliptical's "distance" does not.
DISTANCE_KEYS = frozenset({"running", "walking", "cycling", "swimming"})


@dataclass(frozen=True)
class Filed:
	key: str
	payload: dict[str, Any]


def as_activity(workout: WorkoutIn, zone: dt.tzinfo) -> Filed | None:
	"""A workout as an activity's payload, or None for a type memo doesn't count."""
	known = TYPES.get(workout.activity_type)
	if known is None:
		return None
	key, label = known
	amounts = []
	km = (workout.distance_m or 0) / 1000
	if key in DISTANCE_KEYS and 0 < km <= LIMITS["distance"]:
		amounts.append(stored_amount("distance", km))
	minutes = workout.duration_s / 60
	if 0 < minutes <= LIMITS["duration"]:
		amounts.append(stored_amount("duration", minutes))
	return Filed(
		key=key,
		payload={
			"key": key,
			"label": label,
			"occurred_on": workout.start.astimezone(zone).date().isoformat(),
			"source": HEALTH,
			"health_id": workout.id,
			"started_at": workout.start.isoformat(),
			"amounts": amounts,
		},
	)


def _from_health(user_id: uuid.UUID) -> list[Any]:
	return [
		Derivation.user_id == user_id,
		Derivation.kind == ACTIVITY,
		Derivation.payload["health_id"].astext.is_not(None),
	]


async def sync(session: AsyncSession, user: User, body: HealthSync) -> HealthSyncOut:
	"""File the window's workouts and let go of the ones Health no longer has."""
	await session.execute(
		text("SELECT pg_advisory_xact_lock(:ns, hashtext(:uid))"),
		{"ns": _LOCK_NAMESPACE, "uid": str(user.id)},
	)
	zone = tz_or_utc(user.timezone)
	filed = [f for w in body.workouts if (f := as_activity(w, zone)) is not None]
	if filed:
		stmt = insert(Derivation).values(
			[
				{
					"id": uuid7(),
					"user_id": user.id,
					"capture_id": None,
					"kind": ACTIVITY,
					"payload": f.payload,
					"status": "accepted",
					"extractor_version": VERSION,
					"confidence": 1.0,
				}
				for f in filed
			]
		)
		await session.execute(
			stmt.on_conflict_do_update(
				index_elements=[Derivation.user_id, text("(payload ->> 'health_id')")],
				index_where=HEALTH_WORKOUT_WHERE,
				set_={"payload": stmt.excluded.payload, "updated_at": func.now()},
				# A day you removed stays removed, whatever Health says about it.
				where=Derivation.status != "rejected",
			)
		)
	kept = [f.payload["health_id"] for f in filed]
	started = Derivation.payload["started_at"].astext.cast(DateTime(timezone=True))
	gone = await session.execute(
		delete(Derivation)
		.where(
			*_from_health(user.id),
			Derivation.status != "rejected",
			started >= body.since,
			started <= body.until,
			Derivation.payload["health_id"].astext.not_in(kept),
		)
		.returning(Derivation.id)
	)
	removed = len(gone.all())
	log.info(
		"health.synced",
		user_id=str(user.id),
		workouts=len(body.workouts),
		filed=len(filed),
		removed=removed,
	)
	return HealthSyncOut(
		filed=len(filed), skipped=len(body.workouts) - len(filed), removed=removed
	)


async def forget(session: AsyncSession, user_id: uuid.UUID) -> int:
	"""Apple Health turned off in memo: every workout it read goes, removed days too."""
	gone = await session.execute(
		delete(Derivation).where(*_from_health(user_id)).returning(Derivation.id)
	)
	return len(gone.all())
