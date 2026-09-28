import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field

# mime -> object key extension. Kept narrow on purpose: anything else is a 422.
ALLOWED_AUDIO_MIME: dict[str, str] = {
	"audio/m4a": "m4a",
	"audio/x-m4a": "m4a",
	"audio/mp4": "m4a",
	"audio/aac": "aac",
	"audio/mpeg": "mp3",
	"audio/wav": "wav",
	"audio/x-wav": "wav",
	"audio/webm": "webm",
	"audio/ogg": "ogg",
}

MAX_RECORDING_BYTES = 100 * 1024 * 1024


class RecordingCreate(BaseModel):
	model_config = ConfigDict(extra="forbid")

	mime: str = Field(pattern=r"^audio/[A-Za-z0-9.+-]+$")
	bytes: int | None = Field(default=None, ge=1, le=MAX_RECORDING_BYTES)
	duration_ms: int | None = Field(default=None, ge=1)


class RecordingOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	recording_id: uuid.UUID
	upload_url: str
	expires_at: dt.datetime
