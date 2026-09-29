"""The map of one thing (ADR 0049): what memo has linked to a person, a note, an idea
or a prayer. Read-only; the app re-centres by asking for the neighbour's map."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from memo.auth import CurrentUser
from memo.config import Settings, get_settings
from memo.db import DbSession
from memo.schemas.mindmap import CenterKind, MapOut
from memo.services.mindmap import NotFound, build_map

router = APIRouter(prefix="/map", tags=["map"])


@router.get("", response_model=MapOut)
async def read_map(
	session: DbSession,
	user_id: CurrentUser,
	settings: Annotated[Settings, Depends(get_settings)],
	kind: CenterKind,
	node_id: Annotated[uuid.UUID, Query(alias="id")],
) -> MapOut:
	try:
		return await build_map(
			session,
			user_id,
			kind,
			node_id,
			max_distance=settings.related_max_distance,
		)
	except NotFound as exc:
		raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such {kind}") from exc
