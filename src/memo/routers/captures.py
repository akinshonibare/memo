import base64
import binascii
import datetime as dt
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert

from memo.auth import CurrentUser
from memo.db import DbSession
from memo.ids import uuid7
from memo.models import Capture
from memo.schemas.captures import CaptureCreate, CaptureOut, CapturePage

router = APIRouter(prefix="/captures", tags=["captures"])


def _encode_cursor(capture: Capture) -> str:
	raw = f"{capture.created_at.isoformat()}|{capture.id}".encode()
	return base64.urlsafe_b64encode(raw).decode()


def _decode_cursor(cursor: str) -> tuple[dt.datetime, uuid.UUID]:
	try:
		created_at, capture_id = (
			base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
		)
		return dt.datetime.fromisoformat(created_at), uuid.UUID(capture_id)
	except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
		raise HTTPException(status.HTTP_400_BAD_REQUEST, "invalid cursor") from exc


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CaptureOut)
async def create_capture(
	body: CaptureCreate, session: DbSession, user_id: CurrentUser
) -> Capture:
	stmt = (
		insert(Capture)
		.values(
			id=uuid7(),
			user_id=user_id,
			kind="text",
			text=body.text,
			client_token=body.client_token,
		)
		.on_conflict_do_nothing(constraint="uq_captures_user_client_token")
		.returning(Capture)
	)
	capture = (await session.scalars(stmt)).one_or_none()
	if capture is None:
		# A replay: the client already submitted this token. Return the row it created.
		existing = await session.scalars(
			select(Capture).where(
				Capture.user_id == user_id, Capture.client_token == body.client_token
			)
		)
		capture = existing.one()
	return capture


@router.get("", response_model=CapturePage)
async def list_captures(
	session: DbSession,
	user_id: CurrentUser,
	cursor: str | None = None,
	limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> CapturePage:
	stmt = (
		select(Capture)
		.where(Capture.user_id == user_id)
		.order_by(Capture.created_at.desc(), Capture.id.desc())
		.limit(limit + 1)
	)
	if cursor is not None:
		after_created, after_id = _decode_cursor(cursor)
		stmt = stmt.where(
			or_(
				Capture.created_at < after_created,
				and_(Capture.created_at == after_created, Capture.id < after_id),
			)
		)
	rows = list((await session.scalars(stmt)).all())
	page = rows[:limit]
	next_cursor = _encode_cursor(page[-1]) if len(rows) > limit else None
	return CapturePage(
		items=[CaptureOut.model_validate(c) for c in page], next_cursor=next_cursor
	)


@router.get("/{capture_id}", response_model=CaptureOut)
async def get_capture(
	capture_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> Capture:
	capture = await session.scalar(
		select(Capture).where(Capture.id == capture_id, Capture.user_id == user_id)
	)
	if capture is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	return capture
