"""Derivation payloads: the schema the model is constrained to AND the schema a PATCH
body is validated against (curriculum Phase 3: one Pydantic model for both)."""

import datetime as dt
import re
import uuid
from typing import Any, Literal

from pydantic import (
	BaseModel,
	ConfigDict,
	Field,
	ValidationError,
	field_validator,
	model_validator,
)

Kind = Literal["todo", "event", "idea"]
KINDS: tuple[str, ...] = ("todo", "event", "idea")
DerivationStatus = Literal["pending_review", "confirmed", "rejected", "user_created"]
DERIVATION_STATUSES: tuple[str, ...] = (
	"pending_review",
	"confirmed",
	"rejected",
	"user_created",
)


# A string value that ends in something like `','confidence':1.0` is the model
# spilling the next field into this one. The JSON is still valid, so only a semantic
# check catches it; failing here sends the response through the one-repair loop
# (ADR 0015) instead of storing the junk.
_LEAKED_FIELD = re.compile(r"""['"]\s*,\s*['"]?\w+['"]?\s*:""")


class _Item(BaseModel):
	model_config = ConfigDict(extra="forbid")

	title: str = Field(min_length=1, max_length=200)
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	@field_validator("title")
	@classmethod
	def _title_has_content(cls, value: str) -> str:
		stripped = value.strip()
		if not stripped:
			raise ValueError("title must not be blank")
		return stripped

	@field_validator("*", mode="after")
	@classmethod
	def _no_leaked_fields(cls, value: Any) -> Any:
		if isinstance(value, str) and _LEAKED_FIELD.search(value):
			raise ValueError("text contains what looks like a leaked JSON field")
		return value


class TodoPayload(_Item):
	"""Something the author committed to doing."""

	due: dt.date | None = None
	notes: str | None = Field(default=None, max_length=2000)


class EventPayload(_Item):
	"""Something happening at a specific time."""

	starts_at: dt.datetime
	ends_at: dt.datetime | None = None
	location: str | None = Field(default=None, max_length=200)

	@model_validator(mode="after")
	def _ends_after_starts(self) -> "EventPayload":
		if self.ends_at is not None and self.ends_at < self.starts_at:
			raise ValueError("ends_at must not be before starts_at")
		return self


class IdeaPayload(_Item):
	"""A thought worth coming back to."""

	summary: str = Field(min_length=1, max_length=1000)


PAYLOAD_MODELS: dict[str, type[_Item]] = {
	"todo": TodoPayload,
	"event": EventPayload,
	"idea": IdeaPayload,
}


class Extraction(BaseModel):
	"""What the extractor returns for one capture. Empty lists are a valid answer."""

	model_config = ConfigDict(extra="forbid")

	todos: list[TodoPayload] = Field(default_factory=list)
	events: list[EventPayload] = Field(default_factory=list)
	ideas: list[IdeaPayload] = Field(default_factory=list)

	def items(self) -> list[tuple[str, _Item]]:
		return (
			[("todo", t) for t in self.todos]
			+ [("event", e) for e in self.events]
			+ [("idea", i) for i in self.ideas]
		)


def validate_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
	"""Validate a user-supplied payload with the kind's model; return it normalised."""
	model = PAYLOAD_MODELS[kind]
	return model.model_validate(payload).model_dump(mode="json", exclude={"confidence"})


def validation_detail(exc: ValidationError) -> list[dict[str, Any]]:
	"""A 422 body. exc.errors() itself carries exception objects in `ctx`, which the
	JSON encoder cannot serialise - that turns a 422 into a 500."""
	return [{"loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]


class DerivationOut(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	capture_id: uuid.UUID
	kind: str
	payload: dict[str, Any]
	status: str
	extractor_version: str
	confidence: float | None
	reviewed_at: dt.datetime | None
	created_at: dt.datetime


class DerivationPage(BaseModel):
	items: list[DerivationOut]
	next_cursor: str | None


class DerivationPatch(BaseModel):
	"""Confirm, reject, or edit. An edit with no status is a confirmation."""

	model_config = ConfigDict(extra="forbid")

	status: Literal["confirmed", "rejected"] | None = None
	payload: dict[str, Any] | None = None

	@model_validator(mode="after")
	def _something_to_do(self) -> "DerivationPatch":
		if self.status is None and self.payload is None:
			raise ValueError("provide status, payload, or both")
		return self


class DerivationCreate(BaseModel):
	"""One the extractor missed - a recall failure, recorded as such."""

	model_config = ConfigDict(extra="forbid")

	kind: Kind
	payload: dict[str, Any]
