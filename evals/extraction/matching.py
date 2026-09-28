"""Semantic matching of predicted to expected items, recorded for offline replay.

Title token overlap can't see paraphrase: "Get the eval set done" and "Finish the eval
set" are the same todo and share two words. Here a model decides which predicted item
is which expected one, per kind, one-to-one. Its verdicts are recorded with a
fingerprint of exactly the items it saw, so CI replays them without a key, and a
verdict whose inputs have changed (new golden label, new recording) is ignored rather
than applied to items it never judged.

The matcher is part of the measurement, so it is versioned like a prompt: change
MATCHER_SYSTEM, bump MATCHER_VERSION, and re-run `--match`.
"""

import hashlib
import json
from typing import Any

from anthropic import AsyncAnthropic
from anthropic.lib._parse._transform import transform_schema
from pydantic import BaseModel

from memo.schemas.derivations import KINDS, Extraction

MATCHER_VERSION = "match-v1"

MATCHER_SYSTEM = """\
You score an information-extraction system. You get the items a person labelled as
correct (expected) and the items the system produced (predicted), all of one kind:
todos, events, or ideas. Pair each predicted item with the expected item it is the
same as - the same task, the same event, the same idea - even if worded differently.

- A pair must be the same thing, not merely related: "Book the hotel" and "Compare
  hotel prices" are two different todos.
- Each expected item and each predicted item appears in at most one pair.
- Leave out anything with no counterpart. An empty list is a valid answer.
Refer to items by their index numbers.
"""

Pairs = dict[str, list[tuple[int, int]]]


class _Pair(BaseModel):
	expected: int
	predicted: int


class _Verdict(BaseModel):
	pairs: list[_Pair]


def _describe(kind: str, extraction: Extraction) -> list[str]:
	lines = []
	for item_kind, item in extraction.items():
		if item_kind != kind:
			continue
		detail = [item.title]
		for key in ("summary", "notes", "due", "starts_at", "location"):
			value = getattr(item, key, None)
			if value:
				detail.append(f"{key}: {value}")
		lines.append(" | ".join(str(d) for d in detail))
	return lines


def fingerprint(expected: Extraction, predicted: Extraction) -> str:
	"""Of the exact items judged. A recorded verdict applies only if this matches."""
	payload = {
		kind: [_describe(kind, expected), _describe(kind, predicted)] for kind in KINDS
	}
	body = json.dumps([MATCHER_VERSION, payload], sort_keys=True)
	return hashlib.sha256(body.encode()).hexdigest()[:16]


def _valid(
	pairs: list[_Pair], n_expected: int, n_predicted: int
) -> list[tuple[int, int]]:
	"""Keep in-range, one-to-one pairs; a model's slip must not inflate the score."""
	kept: list[tuple[int, int]] = []
	seen_e: set[int] = set()
	seen_p: set[int] = set()
	for p in pairs:
		if not (0 <= p.expected < n_expected and 0 <= p.predicted < n_predicted):
			continue
		if p.expected in seen_e or p.predicted in seen_p:
			continue
		seen_e.add(p.expected)
		seen_p.add(p.predicted)
		kept.append((p.expected, p.predicted))
	return kept


class LLMMatcher:
	def __init__(self, client: AsyncAnthropic, *, model: str = "claude-opus-5") -> None:
		self._client = client
		self._model = model
		self._schema = transform_schema(_Verdict)

	async def match(self, expected: Extraction, predicted: Extraction) -> Pairs:
		out: Pairs = {}
		for kind in KINDS:
			exp, pred = _describe(kind, expected), _describe(kind, predicted)
			if not exp or not pred:
				out[kind] = []
				continue
			listing = "\n".join(f"E{i}: {line}" for i, line in enumerate(exp))
			listing += "\n\n" + "\n".join(
				f"P{i}: {line}" for i, line in enumerate(pred)
			)
			response = await self._client.messages.create(
				model=self._model,
				max_tokens=2048,
				system=MATCHER_SYSTEM,
				messages=[
					{
						"role": "user",
						"content": (
							f"Kind: {kind}\n\n{listing}\n\n"
							"Index numbers are the digits after E and P."
						),
					}
				],
				output_config={
					"format": {"type": "json_schema", "schema": self._schema}
				},
			)
			if response.stop_reason in ("refusal", "max_tokens"):
				raise RuntimeError(f"matcher stopped: {response.stop_reason}")
			text = next(b.text for b in response.content if b.type == "text")
			verdict = _Verdict.model_validate_json(text)
			out[kind] = _valid(verdict.pairs, len(exp), len(pred))
		return out


def load_matches(raw: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
	if not raw or raw.get("matcher") != MATCHER_VERSION:
		return {}
	examples: dict[str, dict[str, Any]] = raw.get("examples", {})
	return examples


def dump_matches(entries: dict[str, dict[str, Any]]) -> str:
	return json.dumps(
		{"matcher": MATCHER_VERSION, "examples": entries}, indent=2, sort_keys=True
	)
