import datetime as dt
import json
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ConversationOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	title: str | None
	created_at: dt.datetime
	updated_at: dt.datetime


class ConversationPage(BaseModel):
	items: list[ConversationOut]
	next_cursor: str | None


class MessageCreate(BaseModel):
	model_config = ConfigDict(extra="forbid")

	text: str = Field(min_length=1, max_length=4_000)


class MessageAccepted(BaseModel):
	"""Create-then-stream: the assistant message exists before generation starts."""

	conversation_id: uuid.UUID
	user_message_id: uuid.UUID
	message_id: uuid.UUID
	stream_url: str


class CitationOut(BaseModel):
	chunk_id: uuid.UUID
	# None when the chunk is a derivation whose note was deleted (ADR 0022).
	capture_id: uuid.UUID | None
	derivation_id: uuid.UUID | None
	rank: int
	snippet: str
	created_at: dt.datetime


class MessageOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	conversation_id: uuid.UUID
	role: str
	text: str | None
	status: str
	rewritten_query: str | None
	rewrite_ms: int | None
	ttft_ms: int | None
	error: str | None
	created_at: dt.datetime
	completed_at: dt.datetime | None
	citations: list[CitationOut] = Field(default_factory=list)


class UsageEvent(BaseModel):
	input_tokens: int
	output_tokens: int
	cost_usd: str
	ms: int


class ErrorEvent(BaseModel):
	code: str
	message: str
	retryable: bool


def sse(event: str, data: dict[str, Any] | BaseModel) -> str:
	payload = (
		data.model_dump_json()
		if isinstance(data, BaseModel)
		else json.dumps(data, default=str)
	)
	return f"event: {event}\ndata: {payload}\n\n"
