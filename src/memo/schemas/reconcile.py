"""What the reconciler returns (ADR 0048): which earlier items a new note changes.

Validated against the note and the list the model was shown, not only against its own
shape: a label it wasn't given, a prayer "continued" or an idea "answered", or a quote
that isn't in the note fails validation, and goes back to the model once through the
repair loop (ADR 0015). The quote is the evidence the person reads before saying yes,
so it has to be their own words.
"""

import re
import uuid
from typing import Any, Literal, Self

from pydantic import (
	BaseModel,
	ConfigDict,
	Field,
	ValidationInfo,
	field_validator,
	model_validator,
)

from memo.schemas.derivations import no_leaked_field

Relation = Literal["answers", "continues"]
# A prayer is answered; an idea is carried on. P1, P2... and I1, I2... in the prompt.
RELATION_FOR_PREFIX: dict[str, str] = {"P": "answers", "I": "continues"}
QUOTE_MAX = 240


class Change(BaseModel):
	# Every field required in the output schema, for the reason on derivations._Item.
	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	candidate: str = Field(min_length=2, max_length=4)
	relation: Relation
	quote: str = Field(min_length=1, max_length=QUOTE_MAX)
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	_no_leak = field_validator("quote")(no_leaked_field)


_SPACE = re.compile(r"\s+")
_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def plain(text: str) -> str:
	"""Case, curly quotes and runs of spaces don't make a quote a different quote."""
	return _SPACE.sub(" ", text.translate(_QUOTES)).strip().casefold()


class Reconciliation(BaseModel):
	"""The changes one note makes. An empty list is the usual answer."""

	model_config = ConfigDict(extra="forbid")

	changes: list[Change]

	@model_validator(mode="after")
	def _fits_what_was_asked(self, info: ValidationInfo) -> Self:
		context: dict[str, Any] = info.context or {}
		labels: set[str] | None = context.get("labels")
		entry: str | None = context.get("entry")
		if labels is None or entry is None:
			return self  # parsed outside the reader (a replay): shape only
		seen: set[str] = set()
		where = plain(entry)
		for change in self.changes:
			if change.candidate not in labels:
				raise ValueError(f"{change.candidate} is not one of the listed items")
			if change.candidate in seen:
				raise ValueError(f"{change.candidate} is listed twice; give it once")
			seen.add(change.candidate)
			wanted = RELATION_FOR_PREFIX[change.candidate[0]]
			if change.relation != wanted:
				raise ValueError(f"{change.candidate} can only be '{wanted}'")
			if plain(change.quote.strip("\"' ")) not in where:
				raise ValueError(
					f"the quote for {change.candidate} must be copied exactly from the "
					"entry"
				)
		return self


class UpdateTarget(BaseModel):
	id: uuid.UUID
	kind: str
	title: str
	summary: str | None = None
	person_name: str | None = None
	answered_at: Any | None = None


class UpdateOut(BaseModel):
	"""A suggested change, as the app shows it: what, which item, and the words that
	suggest it."""

	id: uuid.UUID
	relation: Relation
	quote: str
	status: str
	capture_id: uuid.UUID | None
	written_at: Any
	target: UpdateTarget


class UpdateDecision(BaseModel):
	model_config = ConfigDict(extra="forbid")

	# yes: answered / carried on. no: it isn't.
	decision: Literal["yes", "no"]
