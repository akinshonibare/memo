"""People (ADR 0029): who a note is about, and whether the author was in touch.

The people stage reads each note with its own prompt (prompts/people_v1.py) into a
PeopleExtraction. Each mention becomes a `mention` derivation, and the resolver in
services/people.py links it to a `people` row - or, when two people could be meant,
leaves it unlinked for the user to answer "which one?".
"""

import datetime as dt
import uuid
from typing import Any, Literal

from pydantic import (
	BaseModel,
	ConfigDict,
	Field,
	ValidationInfo,
	field_validator,
	model_validator,
)

from memo.schemas.derivations import DerivationOut, no_leaked_field

Contact = Literal["in_person", "call", "message", "unspecified", "none"]
CONTACTS: tuple[str, ...] = ("in_person", "call", "message", "unspecified", "none")
# Every value but "none" means the author and the person were actually in touch.
# "none" - mentioned, heard about, planned - never moves the last-contact date.
IN_TOUCH: frozenset[str] = frozenset(CONTACTS) - {"none"}


def _clean(value: str | None) -> str | None:
	"""One line, no control characters, None when blank: these strings are shown as
	names and go back into prompts."""
	if value is None:
		return None
	cleaned = " ".join("".join(ch for ch in value if ch.isprintable()).split())
	return cleaned or None


class MentionPayload(BaseModel):
	"""One person in one note. `name` as written ("Tunde", "Mum"); `full_name` only
	when the note gives more of it; `hint` is whatever tells two people with the same
	name apart ("from work", "my cousin")."""

	# Every field required in the output schema, for the reason on derivations._Item.
	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	name: str = Field(min_length=1, max_length=120)
	full_name: str | None = Field(default=None, max_length=120)
	hint: str | None = Field(default=None, max_length=120)
	contact: Contact = "none"
	occurred_on: dt.date | None = None
	context: str | None = Field(default=None, max_length=500)
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	@field_validator("name")
	@classmethod
	def _name_has_content(cls, value: str) -> str:
		cleaned = _clean(value)
		if cleaned is None:
			raise ValueError("name must not be blank")
		return cleaned

	@field_validator("full_name", "hint", "context")
	@classmethod
	def _tidy(cls, value: str | None) -> str | None:
		return _clean(value)

	@field_validator("*", mode="after")
	@classmethod
	def _no_leaked_fields(cls, value: Any) -> Any:
		return no_leaked_field(value)

	@model_validator(mode="after")
	def _contact_is_in_the_past(self, info: ValidationInfo) -> "MentionPayload":
		# A mention without contact has no contact date; dropping a stray one is
		# harmless. A contact dated after the note was written is a misread ("seeing
		# Tunde Friday" is a plan, not a call), so it fails into the repair loop.
		if self.contact == "none":
			self.occurred_on = None
			return self
		context = info.context if isinstance(info.context, dict) else {}
		captured_on = context.get("captured_on")
		if (
			isinstance(captured_on, dt.date)
			and self.occurred_on is not None
			and self.occurred_on > captured_on
		):
			raise ValueError(
				f"occurred_on {self.occurred_on} is after the note was written "
				f"({captured_on}): contact that has not happened yet is contact 'none'"
			)
		return self


class PeopleExtraction(BaseModel):
	"""What the people reader returns for one note. An empty list is a good answer."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	people: list[MentionPayload] = Field(default_factory=list)


# --- the API --------------------------------------------------------------------


class PersonRef(BaseModel):
	id: uuid.UUID
	name: str
	note: str | None
	last_contact_on: dt.date | None = None


class MentionOut(BaseModel):
	"""A person in a note. `person` is None while memo is waiting for you to say who
	it is; `candidates` are the people it could be, worked out when read."""

	id: uuid.UUID
	capture_id: uuid.UUID | None
	status: str
	confidence: float | None
	name: str
	full_name: str | None
	hint: str | None
	contact: str
	occurred_on: dt.date | None
	context: str | None
	person: PersonRef | None
	candidates: list[PersonRef] = []
	created_at: dt.datetime
	# The note it came from, for a person's timeline and the "who's who" list.
	written_at: dt.datetime | None = None
	excerpt: str | None = None


class PersonOut(BaseModel):
	id: uuid.UUID
	name: str
	aliases: list[str]
	note: str | None
	# None: never remind. New people get the default (DEFAULT_REACH_OUT_DAYS).
	reach_out_every_days: int | None
	snoozed_until: dt.date | None
	last_contact_on: dt.date | None
	mention_count: int
	# Time to reach out: in touch before, not since the rhythm says, not snoozed.
	due: bool
	created_at: dt.datetime


class PeopleList(BaseModel):
	people: list[PersonOut]
	unresolved: int  # mentions waiting for "which one?"
	unread_notes: int  # older notes the people stage has not read yet


class PersonDetail(PersonOut):
	mentions: list[MentionOut]
	open_todos: list[DerivationOut]
	upcoming_events: list[DerivationOut]


class PersonPatch(BaseModel):
	"""Any of: name, aliases, a short note ("from work"), the reach-out rhythm (null
	turns reminders off), and snoozed_until (null clears a snooze)."""

	model_config = ConfigDict(extra="forbid")

	name: str | None = Field(default=None, min_length=1, max_length=120)
	aliases: list[str] | None = Field(default=None, max_length=20)
	note: str | None = Field(default=None, max_length=120)
	reach_out_every_days: int | None = Field(default=None, ge=1, le=365)
	snoozed_until: dt.date | None = None

	@field_validator("name", "note")
	@classmethod
	def _tidy(cls, value: str | None) -> str | None:
		return _clean(value)

	@field_validator("aliases")
	@classmethod
	def _tidy_aliases(cls, value: list[str] | None) -> list[str] | None:
		if value is None:
			return None
		out: list[str] = []
		for alias in value:
			cleaned = _clean(alias)
			if cleaned and len(cleaned) <= 120 and cleaned not in out:
				out.append(cleaned)
		return out

	@model_validator(mode="after")
	def _something(self) -> "PersonPatch":
		if not self.model_fields_set:
			raise ValueError(
				"provide name, aliases, note, reach_out_every_days or snoozed_until"
			)
		if "name" in self.model_fields_set and self.name is None:
			raise ValueError("name must not be blank")
		return self


class MentionPatch(BaseModel):
	"""Exactly one of: this is `person_id`; this is someone new; or confirm / reject
	the link as it stands. Linking or creating also confirms."""

	model_config = ConfigDict(extra="forbid")

	person_id: uuid.UUID | None = None
	new_person: bool | None = None
	status: Literal["confirmed", "rejected"] | None = None

	@model_validator(mode="after")
	def _exactly_one(self) -> "MentionPatch":
		chosen = [
			self.person_id is not None,
			bool(self.new_person),
			self.status is not None,
		]
		if sum(chosen) != 1:
			raise ValueError("provide exactly one of person_id, new_person, status")
		return self


class MergeBody(BaseModel):
	model_config = ConfigDict(extra="forbid")

	into: uuid.UUID


class ContactBody(BaseModel):
	"""'Spoke to them', one tap: logged as a mention you added, on today unless
	another day is given."""

	model_config = ConfigDict(extra="forbid")

	occurred_on: dt.date | None = None
	contact: Literal["in_person", "call", "message", "unspecified"] = "unspecified"


class ReachOut(BaseModel):
	"""A person it may be time to get in touch with, for Today. `latest` is the most
	recent thing a note said about them - the reason to reach out, not a nag."""

	person_id: uuid.UUID
	name: str
	last_contact_on: dt.date
	every_days: int
	latest: str | None
	latest_on: dt.date | None
