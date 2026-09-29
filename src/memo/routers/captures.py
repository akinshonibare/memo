import datetime as dt
import uuid
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import and_, delete, or_, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError

from memo.auth import CurrentUser
from memo.config import Settings, get_settings
from memo.db import DbSession, commit_and_rebind
from memo.ids import uuid7
from memo.models import Capture, Derivation, Recording
from memo.pagination import decode_cursor, encode_cursor
from memo.routers.limits import limit_capture
from memo.routers.recordings import get_object_store
from memo.schemas.captures import (
	AudioOut,
	CaptureCreate,
	CaptureDetail,
	CaptureOut,
	CapturePage,
)
from memo.schemas.derivations import (
	DerivationCreate,
	DerivationOut,
	validate_payload,
	validation_detail,
)
from memo.services.derivation_index import enqueue_reembed
from memo.services.queue import Queue
from memo.services.storage import ObjectStore
from memo.status import CaptureStatus, transition

log = structlog.get_logger("memo.captures")

router = APIRouter(prefix="/captures", tags=["captures"])


def get_queue(request: Request) -> Queue:
	queue: Queue = request.app.state.queue
	return queue


async def _enqueue(queue: Queue, capture: Capture) -> None:
	request_id = structlog.contextvars.get_contextvars().get("request_id")
	try:
		await queue.enqueue_process_capture(capture.id, capture.user_id, request_id)
	except Exception:
		# The row is committed, so nothing is lost: the stale-pending sweep re-enqueues.
		log.exception("enqueue.failed", capture_id=str(capture.id))


async def _owned_capture(
	session: DbSession, capture_id: uuid.UUID, user_id: uuid.UUID
) -> Capture:
	capture = await session.scalar(
		select(Capture).where(Capture.id == capture_id, Capture.user_id == user_id)
	)
	if capture is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND)
	return capture


async def _with_durations(
	session: DbSession, captures: list[Capture], user_id: uuid.UUID
) -> list[CaptureOut]:
	"""CaptureOut rows with duration_ms from the attached recordings, one query."""
	ids = [c.recording_id for c in captures if c.recording_id is not None]
	durations: dict[uuid.UUID, int | None] = {}
	if ids:
		rows = await session.execute(
			select(Recording.id, Recording.duration_ms).where(
				Recording.id.in_(ids), Recording.user_id == user_id
			)
		)
		durations = {rid: ms for rid, ms in rows.all()}
	out = []
	for c in captures:
		item = CaptureOut.model_validate(c)
		if c.recording_id is not None:
			item.duration_ms = durations.get(c.recording_id)
		out.append(item)
	return out


@router.post(
	"",
	status_code=status.HTTP_202_ACCEPTED,
	response_model=CaptureOut,
	dependencies=[Depends(limit_capture)],
)
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
	if body.captured_at is not None:
		values["created_at"] = body.captured_at
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
		kind = "image" if recording.mime.startswith("image/") else "audio"
		values.update(kind=kind, recording_id=recording.id)
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


_RELATED_SQL = text(
	"""
	WITH this AS (
		SELECT avg(embedding) AS centroid FROM chunks
		WHERE capture_id = :cid AND user_id = :uid
			AND derivation_id IS NULL AND superseded_at IS NULL
	)
	SELECT k.capture_id, min(k.embedding <=> this.centroid) AS distance
	FROM chunks k, this
	WHERE this.centroid IS NOT NULL
		AND k.user_id = :uid AND k.capture_id <> :cid AND k.capture_id IS NOT NULL
		AND k.derivation_id IS NULL AND k.superseded_at IS NULL
	GROUP BY k.capture_id
	ORDER BY distance
	LIMIT :n
	"""
)


class RelatedOut(BaseModel):
	capture: CaptureOut
	similarity: float


@router.get("/{capture_id}/related", response_model=list[RelatedOut])
async def related_captures(
	capture_id: uuid.UUID,
	session: DbSession,
	user_id: CurrentUser,
	settings: Annotated[Settings, Depends(get_settings)],
	limit: Annotated[int, Query(ge=1, le=10)] = 3,
) -> list[RelatedOut]:
	"""Notes about the same thing (ADR 0028): the nearest other notes to this one's
	embedding centroid. No model call; the threshold keeps it quiet rather than always
	showing *something*, because a wrong "related" is worse than none."""
	await _owned_capture(session, capture_id, user_id)
	rows = (
		await session.execute(
			_RELATED_SQL, {"cid": capture_id, "uid": user_id, "n": limit}
		)
	).all()
	near = {
		cid: float(distance)
		for cid, distance in rows
		if distance is not None and distance <= settings.related_max_distance
	}
	if not near:
		return []
	captures = (
		await session.scalars(
			select(Capture).where(
				Capture.id.in_(near.keys()), Capture.user_id == user_id
			)
		)
	).all()
	out = [
		RelatedOut(capture=CaptureOut.model_validate(c), similarity=1 - near[c.id])
		for c in captures
	]
	return sorted(out, key=lambda r: -r.similarity)


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
		after_created, after_id = decode_cursor(cursor)
		stmt = stmt.where(
			or_(
				Capture.created_at < after_created,
				and_(Capture.created_at == after_created, Capture.id < after_id),
			)
		)
	rows = list((await session.scalars(stmt)).all())
	page = rows[:limit]
	next_cursor = (
		encode_cursor(page[-1].created_at, page[-1].id) if len(rows) > limit else None
	)
	return CapturePage(
		items=await _with_durations(session, page, user_id), next_cursor=next_cursor
	)


@router.get("/{capture_id}", response_model=CaptureDetail)
async def get_capture(
	capture_id: uuid.UUID, session: DbSession, user_id: CurrentUser
) -> CaptureDetail:
	"""The capture, its processing status, and its derivations (design doc, Step 3)."""
	capture = await _owned_capture(session, capture_id, user_id)
	derivations = await session.scalars(
		select(Derivation)
		.where(Derivation.capture_id == capture.id, Derivation.user_id == user_id)
		.order_by(Derivation.created_at, Derivation.id)
	)
	(item,) = await _with_durations(session, [capture], user_id)
	return CaptureDetail(
		**item.model_dump(),
		derivations=[DerivationOut.model_validate(d) for d in derivations.all()],
	)


@router.get("/{capture_id}/media", response_model=AudioOut)
@router.get("/{capture_id}/audio", response_model=AudioOut, include_in_schema=False)
async def get_capture_media(
	capture_id: uuid.UUID,
	session: DbSession,
	user_id: CurrentUser,
	settings: Annotated[Settings, Depends(get_settings)],
	store: Annotated[ObjectStore, Depends(get_object_store)],
) -> AudioOut:
	"""A time-limited URL for the capture's recording or photo. Bytes come from the
	bucket, never through the API. `/audio` is the pre-photo path installed builds
	still call."""
	capture = await _owned_capture(session, capture_id, user_id)
	if capture.recording_id is None:
		raise HTTPException(status.HTTP_404_NOT_FOUND, "capture has no recording")
	recording = await session.scalar(
		select(Recording).where(
			Recording.id == capture.recording_id, Recording.user_id == user_id
		)
	)
	if recording is None or not await store.exists(recording.storage_key):
		raise HTTPException(status.HTTP_404_NOT_FOUND, "recording is gone")
	ttl = settings.upload_url_ttl_seconds
	return AudioOut(
		url=await store.presign_get(recording.storage_key, ttl),
		expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=ttl),
		mime=recording.mime,
		duration_ms=recording.duration_ms,
	)


@router.delete("/{capture_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_capture(
	capture_id: uuid.UUID,
	session: DbSession,
	user_id: CurrentUser,
	store: Annotated[ObjectStore, Depends(get_object_store)],
) -> None:
	"""Delete the note, keep what the user got out of it (ADR 0022).

	Gone: the text, the recording (bytes removed from the bucket first, while the row
	still knows its key), every text chunk, and the derivations the user rejected.
	Kept, with capture_id set to NULL by the foreign key: the todos, events and ideas
	that weren't rejected, and their chunks, so Ask and Today still see them.
	Citations of deleted chunks cascade with them. Nothing is soft-deleted: a text
	chunk left behind would keep answering questions about a note that is gone. The
	database removes text chunks itself (a BEFORE DELETE trigger, migration 0008), so
	no deletion path - this route, delete-everything, a user cascade - can forget."""
	capture = await _owned_capture(session, capture_id, user_id)
	await session.execute(
		delete(Derivation).where(
			Derivation.capture_id == capture.id,
			Derivation.user_id == user_id,
			Derivation.status == "rejected",
		)
	)
	if capture.recording_id is not None:
		recording = await session.scalar(
			select(Recording).where(
				Recording.id == capture.recording_id, Recording.user_id == user_id
			)
		)
		if recording is not None:
			await store.delete(recording.storage_key)
			await session.delete(recording)
	await session.delete(capture)
	await session.flush()
	log.info("capture.deleted", capture_id=str(capture_id))


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
	capture = await _owned_capture(session, capture_id, user_id)
	if capture.status != CaptureStatus.FAILED.value:
		raise HTTPException(
			status.HTTP_409_CONFLICT, "only a failed capture can be retried"
		)
	transition(capture, CaptureStatus.PENDING)
	await commit_and_rebind(session, user_id)
	await _enqueue(queue, capture)
	return capture


@router.post(
	"/{capture_id}/derivations",
	status_code=status.HTTP_201_CREATED,
	response_model=DerivationOut,
)
async def add_derivation(
	capture_id: uuid.UUID,
	body: DerivationCreate,
	session: DbSession,
	user_id: CurrentUser,
	queue: Annotated[Queue, Depends(get_queue)],
) -> Derivation:
	"""The extractor missed one. A recall failure, recorded as `user_created` so it
	is distinguishable from a PATCH (a precision failure) in the eval intake."""
	capture = await _owned_capture(session, capture_id, user_id)
	try:
		payload = validate_payload(body.kind, body.payload)
	except ValidationError as exc:
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, validation_detail(exc)
		) from exc
	derivation = Derivation(
		id=uuid7(),
		user_id=user_id,
		capture_id=capture.id,
		kind=body.kind,
		payload=payload,
		status="user_created",
		extractor_version="user",
		confidence=None,
		reviewed_at=dt.datetime.now(dt.UTC),
	)
	session.add(derivation)
	await commit_and_rebind(session, user_id)
	await enqueue_reembed(queue, derivation)
	return derivation
