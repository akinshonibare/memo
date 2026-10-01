"""Lists (ADR 0069): named lists you keep, and the items a note puts on them."""

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memo.schemas.derivations import no_leaked_field


class ListItemFound(BaseModel):
	"""One thing the lists reader found to put on a list (prompts/lists_v1). Every
	field is required in the output schema, for the reason on derivations._Item."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	list: str = Field(min_length=1, max_length=80)
	title: str = Field(min_length=1, max_length=200)
	quantity: str | None = Field(default=None, max_length=40)
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	@field_validator("list", "title")
	@classmethod
	def _has_content(cls, value: str) -> str:
		stripped = value.strip()
		if not stripped:
			raise ValueError("must not be blank")
		return stripped

	@field_validator("list", "title", "quantity", mode="after")
	@classmethod
	def _no_leaked_fields(cls, value: str | None) -> str | None:
		return no_leaked_field(value)  # type: ignore[no-any-return]


class ListsExtraction(BaseModel):
	"""What the lists reader returns for one note. An empty list is the usual answer."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	items: list[ListItemFound] = Field(default_factory=list)


# --- the API --------------------------------------------------------------------------


class ListOut(BaseModel):
	id: uuid.UUID
	name: str
	open_count: int
	done_count: int
	archived: bool
	created_at: dt.datetime
	updated_at: dt.datetime


class ListItemOut(BaseModel):
	id: uuid.UUID
	title: str
	quantity: str | None
	done_at: dt.datetime | None
	capture_id: uuid.UUID | None  # the note it came from; null when added by hand
	added_by_hand: bool
	created_at: dt.datetime


class ListDetail(ListOut):
	items: list[ListItemOut]


class ListsOut(BaseModel):
	lists: list[ListOut]


class ListCreate(BaseModel):
	name: str = Field(min_length=1, max_length=80)


class ListPatch(BaseModel):
	name: str | None = Field(default=None, min_length=1, max_length=80)
	archived: bool | None = None

	@model_validator(mode="after")
	def _something(self) -> "ListPatch":
		if self.name is None and self.archived is None:
			raise ValueError("provide name, archived, or both")
		return self


class ListItemCreate(BaseModel):
	title: str = Field(min_length=1, max_length=200)
	quantity: str | None = Field(default=None, max_length=40)


class ListItemPatch(BaseModel):
	title: str | None = Field(default=None, min_length=1, max_length=200)
	quantity: str | None = Field(default=None, max_length=40)
	done: bool | None = None

	@model_validator(mode="after")
	def _something(self) -> "ListItemPatch":
		if self.title is None and self.quantity is None and self.done is None:
			raise ValueError("provide title, quantity, done, or a combination")
		return self
