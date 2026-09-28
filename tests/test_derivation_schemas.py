import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from memo.schemas.derivations import (
	Extraction,
	IdeaPayload,
	TodoPayload,
	validate_payload,
)

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"


def _raw(name: str) -> str:
	return (FIXTURES / name).read_text()


def test_recorded_mixed_output_parses() -> None:
	extraction = Extraction.model_validate_json(_raw("mixed_valid.json"))
	assert [t.title for t in extraction.todos] == ["Submit the expense report"]
	assert extraction.todos[0].due is not None and extraction.todos[0].due.day == 2
	assert extraction.events[0].starts_at.hour == 7
	assert extraction.items()[0][0] == "todo"


def test_empty_output_is_valid() -> None:
	assert Extraction.model_validate_json(_raw("empty.json")).items() == []


def test_event_ending_before_it_starts_is_rejected() -> None:
	with pytest.raises(ValidationError, match="ends_at must not be before"):
		Extraction.model_validate_json(_raw("event_ends_before_start.json"))


def test_blank_title_is_rejected() -> None:
	with pytest.raises(ValidationError, match="title must not be blank"):
		Extraction.model_validate_json(_raw("blank_title.json"))


def test_unknown_fields_are_rejected() -> None:
	raw = json.dumps({"todos": [{"title": "x", "priority": "high"}]})
	with pytest.raises(ValidationError):
		Extraction.model_validate_json(raw)


def test_validate_payload_normalises_and_drops_confidence() -> None:
	out = validate_payload("todo", {"title": "  Call the bank ", "confidence": 0.3})
	assert out == {"title": "Call the bank", "due": None, "notes": None}
	with pytest.raises(ValidationError):
		validate_payload("event", {"title": "x"})


def test_leaked_json_in_a_text_field_is_rejected() -> None:
	# Seen in production: the model closed the summary string, then kept writing the
	# next field inside it. Valid JSON, wrong content, so it has to fail validation.
	with pytest.raises(ValidationError):
		IdeaPayload(
			title="Weekly digest",
			summary="Send myself a digest each week.','confidence':1.0",
		)
	with pytest.raises(ValidationError):
		TodoPayload(title="Call Tunde", notes='about pricing", "due": "2026-10-02')
	# Ordinary punctuation is fine.
	IdeaPayload(title="Quote", summary="She said 'no, thanks': that was it.")
