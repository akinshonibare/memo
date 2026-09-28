import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field


class CaptureCreate(BaseModel):
	# extra="forbid" so a user_id smuggled into the body is a 422, not silently ignored.
	model_config = ConfigDict(extra="forbid")

	text: str = Field(min_length=1, max_length=50_000)
	client_token: str = Field(
		min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._~-]+$"
	)


class CaptureOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	kind: str
	text: str | None
	status: str
	failure_reason: str | None
	attempt_count: int
	created_at: dt.datetime


class CapturePage(BaseModel):
	items: list[CaptureOut]
	next_cursor: str | None
