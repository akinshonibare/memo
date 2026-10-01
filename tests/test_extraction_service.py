"""The repair-then-fail policy from ADR 0015, against a scripted provider."""

import datetime as dt
from pathlib import Path

import pytest
from anthropic.types import MessageParam

from memo.services.extraction import ExtractionFailed, Extractor, RawCompletion
from memo.services.retry import RetryableError

FIXTURES = Path(__file__).parent / "fixtures" / "extraction"
NOW = dt.datetime(2026, 9, 28, 9, 0, tzinfo=dt.UTC)


class ScriptedProvider:
	model = "claude-test"

	def __init__(self, outputs: list[str | Exception]) -> None:
		self._outputs = list(outputs)
		self.requests: list[list[MessageParam]] = []

	async def complete(
		self, *, system: str, messages: list[MessageParam]
	) -> RawCompletion:
		self.requests.append(messages)
		out = self._outputs.pop(0)
		if isinstance(out, Exception):
			raise out
		return RawCompletion(
			text=out,
			model=self.model,
			input_tokens=500,
			output_tokens=80,
			cache_read_tokens=0,
			cache_write_tokens=0,
			latency_ms=120,
		)


def _fixture(name: str) -> str:
	return (FIXTURES / name).read_text()


async def test_valid_output_is_returned_with_one_priced_call() -> None:
	provider = ScriptedProvider([_fixture("mixed_valid.json")])
	result = await Extractor(provider).extract("x", captured_at=NOW, timezone="UTC")

	assert [t.title for t in result.extraction.todos] == ["Submit the expense report"]
	assert len(result.calls) == 1
	assert result.calls[0].stage == "extract"
	assert result.calls[0].input_tokens == 500
	assert "Monday" in str(provider.requests[0][0]["content"])  # 2026-09-28


async def test_semantic_failure_is_repaired_once_with_the_error_appended() -> None:
	provider = ScriptedProvider(
		[_fixture("event_ends_before_start.json"), _fixture("mixed_valid.json")]
	)
	result = await Extractor(provider).extract("x", captured_at=NOW, timezone="UTC")

	assert len(result.calls) == 2
	repair_turn = provider.requests[1]
	assert repair_turn[1]["role"] == "assistant"
	assert "ends_at must not be before starts_at" in repair_turn[2]["content"]
	assert result.extraction.events[0].title == "Gym"


async def test_second_failure_is_terminal_and_keeps_the_raw_output() -> None:
	provider = ScriptedProvider(
		[_fixture("blank_title.json"), _fixture("blank_title.json")]
	)
	with pytest.raises(ExtractionFailed) as info:
		await Extractor(provider).extract("x", captured_at=NOW, timezone="UTC")

	assert "schema_validation after 2 attempts" in str(info.value)
	assert "title must not be blank" in str(info.value)
	assert '"title": "   "' in info.value.raw
	assert len(info.value.calls) == 2


async def test_provider_transient_errors_propagate_as_retryable() -> None:
	provider = ScriptedProvider([RetryableError("anthropic 529")])
	with pytest.raises(RetryableError):
		await Extractor(provider).extract("x", captured_at=NOW, timezone="UTC")


def test_version_stamps_prompt_and_model() -> None:
	assert Extractor(ScriptedProvider([])).version == "extract-v1.3/claude-test"


# --- marked highlights (ADR 0068) ---------------------------------------------------

MARKED = (
	"note to self: the owner role bypasses RLS so tests can pass for the wrong reason"
)
EMPTY = '{"todos": [], "events": [], "ideas": []}'


async def test_a_marked_passage_always_becomes_an_idea() -> None:
	# The model ignored the mark: the passage stands in, titled from its first words.
	provider = ScriptedProvider([EMPTY])
	result = await Extractor(provider).extract(MARKED, captured_at=NOW, timezone="UTC")
	first = provider.requests[0][0]["content"]
	assert isinstance(first, str)
	assert "Marked as highlights by the author" in first
	assert "- The owner role bypasses RLS" in first
	[idea] = result.extraction.ideas
	assert idea.summary.startswith("The owner role bypasses RLS")
	assert (
		idea.title
		== "The owner role bypasses RLS so tests can pass for the wrong reason"
	)
	assert idea.confidence == 1.0
	assert result.marked_ideas == frozenset({0})


async def test_the_models_idea_for_a_mark_is_kept_not_doubled() -> None:
	written = (
		'{"todos": [], "events": [], "ideas": [{"title": "Owner bypasses RLS", '
		'"summary": "The table owner bypasses RLS, so tests can pass for the wrong '
		'reason.", "confidence": 0.9}]}'
	)
	result = await Extractor(ScriptedProvider([written])).extract(
		MARKED, captured_at=NOW, timezone="UTC"
	)
	assert [i.title for i in result.extraction.ideas] == ["Owner bypasses RLS"]
	assert result.marked_ideas == frozenset({0})


async def test_a_second_mark_the_model_skipped_is_added_beside_the_first() -> None:
	text = (
		"Insight: I only write when I'm anxious. "
		"Remember this: Ife starts school in September."
	)
	written = (
		'{"todos": [], "events": [], "ideas": [{"title": "Writing and anxiety", '
		'"summary": "I only write when I am anxious.", "confidence": 0.8}]}'
	)
	result = await Extractor(ScriptedProvider([written])).extract(
		text, captured_at=NOW, timezone="UTC"
	)
	assert [i.title for i in result.extraction.ideas] == [
		"Writing and anxiety",
		"Ife starts school in September",
	]
	assert result.marked_ideas == frozenset({0, 1})


async def test_an_unmarked_note_sends_no_marks_and_marks_nothing() -> None:
	provider = ScriptedProvider([EMPTY])
	result = await Extractor(provider).extract(
		"had a long day, back to back meetings", captured_at=NOW, timezone="UTC"
	)
	first = provider.requests[0][0]["content"]
	assert isinstance(first, str) and "Marked as highlights" not in first
	assert result.extraction.ideas == [] and result.marked_ideas == frozenset()
