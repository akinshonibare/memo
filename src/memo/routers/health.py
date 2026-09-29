"""Apple Health (ADR 0050): the phone sends the workouts it read; memo files them as
activities. Only while Habits is on: workouts are only ever read to count habits."""

from fastapi import APIRouter, HTTPException, status

from memo.auth import CurrentUser
from memo.db import DbSession, commit_and_rebind
from memo.models import User
from memo.schemas.health import HealthSync, HealthSyncOut
from memo.services import health

# /workouts, not /health: /health is the unauthenticated liveness check.
router = APIRouter(prefix="/workouts", tags=["health"])


@router.put("", response_model=HealthSyncOut)
async def sync_workouts(
	body: HealthSync, session: DbSession, user_id: CurrentUser
) -> HealthSyncOut:
	"""Every workout in Health between `since` and `until`. Idempotent: the same window
	sent twice changes nothing the second time."""
	user = await session.get(User, user_id)
	if user is None or not user.habits_enabled:
		raise HTTPException(
			status.HTTP_409_CONFLICT, "turn on Habits to read workouts from Health"
		)
	out = await health.sync(session, user, body)
	await commit_and_rebind(session, user_id)
	return out


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def forget_workouts(session: DbSession, user_id: CurrentUser) -> None:
	"""Apple Health turned off in memo: every workout it read is deleted."""
	await health.forget(session, user_id)
	await commit_and_rebind(session, user_id)
