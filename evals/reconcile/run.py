"""Run the reconcile eval over the golden set and gate on the thresholds (ADR 0048).

	uv run python -m evals.reconcile.run              # live; needs ANTHROPIC_API_KEY
	uv run python -m evals.reconcile.run --record evals/reconcile/recorded.json
	uv run python -m evals.reconcile.run --replay evals/reconcile/recorded.json  # CI

Each case is a note and the earlier items the app would show the model. The gates in
front of the model (the answer-ish words, the idea distance) are not measured here:
this measures the decision, given candidates. Answer-gate misses are counted
separately, as the faith eval does for the prayer gate.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from evals.reconcile.scoring import Report, score_example
from memo.config import get_settings
from memo.prompts.reconcile_v1 import Listed
from memo.schemas.reconcile import Reconciliation
from memo.services.extraction import AnthropicExtractionProvider, ExtractionFailed
from memo.services.reconcile import Reconciler, ReconcilerLike, might_answer
from memo.services.retry import TerminalError

GOLDEN = Path(__file__).with_name("golden.jsonl")
CAPTURED_AT = dt.datetime.fromisoformat("2026-09-28T09:00:00+01:00")
TIMEZONE = "Europe/London"
WRITTEN_ON = dt.date(2026, 9, 1)


class GoldenExample(BaseModel):
	id: str
	text: str
	listed: list[tuple[str, str, str | None]]
	expected: list[tuple[str, str]]
	note: str | None = None

	def items(self) -> list[Listed]:
		return [
			Listed(label, title, detail, WRITTEN_ON)
			for label, title, detail in self.listed
		]


def load_golden(path: Path = GOLDEN) -> list[GoldenExample]:
	return [
		GoldenExample.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


def gate_misses(examples: list[GoldenExample]) -> list[str]:
	"""Cases answering a prayer that the app would never compare, because the note has
	no answer-ish word. Must be empty."""
	return [
		e.id
		for e in examples
		if any(relation == "answers" for _, relation in e.expected)
		and not might_answer(e.text)
	]


async def run(
	examples: list[GoldenExample],
	reader: ReconcilerLike | None,
	replay: dict[str, str] | None,
	record_to: Path | None,
) -> Report:
	report = Report.empty()
	recorded: dict[str, str] = {}
	for example in examples:
		empty = Reconciliation(changes=[])
		if replay is not None:
			try:
				predicted = Reconciliation.model_validate_json(
					replay.get(example.id) or '{"changes": []}'
				)
			except ValidationError:
				predicted = empty  # a recorded failure keeps its raw: a miss
		else:
			assert reader is not None
			try:
				result = await reader.read(
					example.text,
					example.items(),
					captured_at=CAPTURED_AT,
					timezone=TIMEZONE,
				)
				predicted, recorded[example.id] = result.result, result.raw
			except ExtractionFailed as exc:
				predicted, recorded[example.id] = empty, exc.raw
			except TerminalError as exc:
				print(f"{example.id}: {exc}", file=sys.stderr)
				predicted, recorded[example.id] = empty, ""
		report.add(example.id, score_example(example.expected, predicted))
	if record_to is not None:
		record_to.write_text(json.dumps(recorded, indent=2) + "\n")
	return report


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--replay", type=Path, help="JSON of {id: raw output}")
	parser.add_argument("--record", type=Path, help="write raw outputs here")
	parser.add_argument("--model", default=None)
	args = parser.parse_args(argv)

	examples = load_golden()
	replay = json.loads(args.replay.read_text()) if args.replay else None
	reader: ReconcilerLike | None = None
	if replay is None:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		reader = Reconciler(
			AnthropicExtractionProvider(
				client,
				model=args.model or settings.extraction_model,
				schema=Reconciliation,
			)
		)
	report = asyncio.run(run(examples, reader, replay, args.record))
	print(f"{report.examples} examples")
	print(report.table())
	for miss in report.misses:
		print("  miss:", miss)
	failures = report.failures()
	skipped = gate_misses(examples)
	if skipped:
		failures.append(f"the answer-word gate would skip {', '.join(skipped)}")
	if failures:
		print("BELOW THRESHOLD:", "; ".join(failures))
		return 1
	print("all thresholds met")
	return 0


if __name__ == "__main__":
	sys.exit(main())
