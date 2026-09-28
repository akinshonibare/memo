import datetime as dt
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from memo.auth import CurrentUser
from memo.config import Settings, get_settings
from memo.db import DbSession
from memo.ids import uuid7
from memo.models import Recording
from memo.schemas.recordings import ALLOWED_AUDIO_MIME, RecordingCreate, RecordingOut
from memo.services.storage import ObjectStore

router = APIRouter(prefix="/recordings", tags=["recordings"])


def get_object_store(request: Request) -> ObjectStore:
	store: ObjectStore = request.app.state.object_store
	return store


@router.post("", status_code=status.HTTP_201_CREATED, response_model=RecordingOut)
async def create_recording(
	body: RecordingCreate,
	session: DbSession,
	user_id: CurrentUser,
	settings: Annotated[Settings, Depends(get_settings)],
	store: Annotated[ObjectStore, Depends(get_object_store)],
) -> RecordingOut:
	extension = ALLOWED_AUDIO_MIME.get(body.mime)
	if extension is None:
		raise HTTPException(
			status.HTTP_422_UNPROCESSABLE_CONTENT, f"unsupported audio type {body.mime}"
		)
	recording_id = uuid7()
	recording = Recording(
		id=recording_id,
		user_id=user_id,
		storage_key=f"{user_id}/{recording_id}.{extension}",
		mime=body.mime,
		bytes=body.bytes,
		duration_ms=body.duration_ms,
	)
	session.add(recording)
	await session.flush()

	ttl = settings.upload_url_ttl_seconds
	upload_url = await store.presign_put(recording.storage_key, body.mime, ttl)
	return RecordingOut(
		recording_id=recording.id,
		upload_url=upload_url,
		expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(seconds=ttl),
	)
