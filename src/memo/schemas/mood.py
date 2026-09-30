"""Mood (ADR 0058): how you sounded in your notes, day by day, on the Within screen.

memo's read of a note is a guess; your own word for a day outranks every read of it.
"""

import datetime as dt
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from memo.schemas.derivations import no_leaked_field

# Five steps, low to bright. The app names them; the server only ever sees numbers.
VALENCE_MIN, VALENCE_MAX = -2, 2
MAX_WORDS = 3
EVIDENCE_CHARS = 120

# memo describes how a note sounds; it never assesses anyone. A clinical word from the
# reader goes back for repair rather than onto someone's screen.
_CLINICAL = re.compile(
	r"\b(depress\w*|manic|mania|anxiety|trauma\w*|ptsd|bipolar|suicid\w*|disorder"
	r"|panic attack|clinical\w*|psychotic)\b",
	re.IGNORECASE,
)


class MoodRead(BaseModel):
	"""What the mood reader returns for one note (prompts/mood_v1). Every field is
	required in the output schema, for the reason given on derivations._Item."""

	model_config = ConfigDict(
		extra="forbid", json_schema_serialization_defaults_required=True
	)

	valence: int | None = Field(default=None, ge=VALENCE_MIN, le=VALENCE_MAX)
	words: list[str] = Field(default_factory=list, max_length=MAX_WORDS)
	evidence: str | None = Field(default=None, max_length=EVIDENCE_CHARS)
	confidence: float = Field(default=1.0, ge=0.0, le=1.0)

	@field_validator("words")
	@classmethod
	def _plain_words(cls, value: list[str]) -> list[str]:
		words = []
		for word in value:
			cleaned = str(no_leaked_field(word.strip().lower()))
			if not cleaned or len(cleaned) > 24:
				raise ValueError(f"not a feeling word: {word!r}")
			if _CLINICAL.search(cleaned):
				raise ValueError(f"an everyday word, not a clinical one: {word!r}")
			if cleaned not in words:
				words.append(cleaned)
		return words

	@field_validator("evidence")
	@classmethod
	def _evidence_has_content(cls, value: str | None) -> str | None:
		if value is None:
			return None
		stripped = value.strip()
		return stripped or None

	@model_validator(mode="after")
	def _null_means_nothing_else(self) -> "MoodRead":
		if self.valence is None and (self.words or self.evidence):
			raise ValueError("valence is null: words must be empty and evidence null")
		if self.valence is not None and not self.evidence:
			raise ValueError("a valence needs the phrase that shows it, as evidence")
		return self


class MoodDayOut(BaseModel):
	"""One day on the Within screen. `valence` is your word if you gave one (`yours`),
	else memo's reads of that day's notes, averaged; null when neither says anything."""

	day: dt.date
	valence: int | None
	yours: bool
	words: list[str]
	notes: int  # how many of that day's notes showed a mood


class MoodWeekOut(BaseModel):
	days: list[MoodDayOut]  # oldest first, ending today
	# One sentence about those days, made from them (no model call); null when memo
	# has nothing to say yet.
	summary: str | None


class MoodReadOut(BaseModel):
	capture_id: uuid.UUID
	# The note's first line, as Home shows it.
	title: str
	created_at: dt.datetime
	valence: int
	words: list[str]
	evidence: str | None


class MoodDayDetail(BaseModel):
	day: MoodDayOut
	reads: list[MoodReadOut]  # in the order the notes were written


class SetMood(BaseModel):
	model_config = ConfigDict(extra="forbid")

	valence: int = Field(ge=VALENCE_MIN, le=VALENCE_MAX)
