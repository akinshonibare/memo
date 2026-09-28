from fastapi import APIRouter

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.services.morning import MorningViewOut, build_morning_view, todays_view

router = APIRouter(prefix="/morning", tags=["morning"])


@router.get("", response_model=MorningViewOut)
async def morning(session: DbSession, user_id: CurrentUser) -> MorningViewOut:
	"""Today's view. Normally built overnight by the worker; built on demand the first
	time so the read path works before the first night has passed."""
	view = await todays_view(session, user_id)
	if view is None:
		view = await build_morning_view(session, user_id)
	return view
