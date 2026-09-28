import base64
import binascii
import datetime as dt
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import and_, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from memo.auth import CurrentUser
from memo.db import DbSession, commit_and_rebind
from memo.ids import uuid7
from memo.models import Capture, Recording
from memo.routers.recordings import get_object_store
from memo.schemas.captures import CaptureCreate, CaptureOut, CapturePage
from memo.services.queue import Queue
from memo.services.storage import ObjectStore
from memo.status import CaptureStatus, transition

log = structlog.get_logger("memo.captures")

router = APIRouter(prefix="/captures", tags=["captures"])


def get_queue(request: Request) -> Queue:
	queue: Queue = request.app.state.queue
	return queue


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


async def _enqueue(queue: Queue, capture: Capture) -> None:
	request_id = structlog.contextvars.get_contextvars().get("request_id")
	try:
		await queue.enqueue_process_capture(capture.id, capture.user_id, request_id)
	except Exception:
		# The row is committed, so nothing is lost: the stale-pending sweep re-enqueues.
		log.exception("enqueue.failed", capture_id=str(capture.id))


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=CaptureOut)
async def create_capture(
	body: CaptureCreate,
	session: DbSession,
	user_id: CurrentUser,
	store: Annotated[ObjectStore, Depends(get_object_store)],
	queue: Annotated[Queue, Depends(get_queue)],
) -> Capture:
	values: dict[str, Any] = {
		"id": uuid7(),
		"user_id": user_id,
		"client_token": body.client_token,
	}
	if body.recording_id is not None:
		recording = await session.scalar(
			select(Recording).where(
				Recording.id == body.recording_id, Recording.user_id == user_id
			)
		)
		if recording is None:
			raise HTTPException(status.HTTP_404_NOT_FOUND, "recording not found")
		if not await store.exists(recording.storage_key):
			raise HTTPException(
				status.HTTP_409_CONFLICT, "recording has not been uploaded yet"
			)
		values.update(kind="audio", recording_id=recording.id)
	else:
		values.update(kind="text", text=body.text)

	stmt = (
		insert(Capture)
		.values(**values)
		.on_conflict_do_nothing(constraint="uq_captures_user_client_token")
		.returning(Capture)
	)
	try:
		capture = (await session.scalars(stmt)).one_or_none()
	except IntegrityError as exc:
		raise HTTPException(
			status.HTTP_409_CONFLICT, "recording is already attached to a capture"
		) from exc
	if capture is None:
		# A replay: the client already submitted this token. Return the row it created;
		# it was enqueued the first time.
		existing = await session.scalars(
			select(Capture).where(
				Capture.user_id == user_id, Capture.client_token == body.client_token
			)
		)
		return existing.one()

	# Commit before enqueueing: the durability boundary is the committed row, and a
	# worker that dequeues before the commit would find nothing.
	await commit_and_rebind(session, user_id)
	await _enqueue(queue, capture)
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


@router.post(
	"/{capture_id}/retry",
	status_code=status.HTTP_202_ACCEPTED,
	response_model=CaptureOut,
)
async def retry_capture(
	capture_id: uuid.UUID,
	session: DbSession,
	user_id: CurrentUser,
	queue: Annotated[Queue, Depends(get_queue)],
) -> Capture:
	"""Dead-letter exit: the user asks for another go (issue #19)."""
	capture = await session.scalar(
		select(Capture).where(Capture.id == capture_id, Capture.user_id == user_id)
	)
	if capture is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	if capture.status != CaptureStatus.FAILED.value:
		raise HTTPException(
			status.HTTP_409_CONFLICT, "only a failed capture can be retried"
		)
	transition(capture, CaptureStatus.PENDING)
	await commit_and_rebind(session, user_id)
	await _enqueue(queue, capture)
	return capture
