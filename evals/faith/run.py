"""Run the faith-reader eval over the golden set and gate on the thresholds.

	uv run python -m evals.faith.run              # live; needs ANTHROPIC_API_KEY
	uv run python -m evals.faith.run --record evals/faith/recorded.json
	uv run python -m evals.faith.run --replay evals/faith/recorded.json  # CI

Every case is sent to the reader, including the ones the prayer-word gate would skip in
the app: the eval measures the prompt, and gate misses are counted separately (below),
so a prayer the gate would never let through shows up as a gate miss, not a pass.

Exit code 1 when any metric is below its bar - the merge gate, as for people.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from evals.faith.scoring import Expected, Report, score_example
from memo.config import get_settings
from memo.schemas.faith import FaithExtraction
from memo.services.extraction import AnthropicExtractionProvider, ExtractionFailed
from memo.services.faith import FaithReader, FaithReaderLike, might_pray
from memo.services.retry import TerminalError

GOLDEN = Path(__file__).with_name("golden.jsonl")
CAPTURED_AT = dt.datetime.fromisoformat("2026-09-28T09:00:00+01:00")
TIMEZONE = "Europe/London"


class GoldenExample(BaseModel):
	id: str
	text: str
	expected: list[Expected]
	note: str | None = None


def load_golden(path: Path = GOLDEN) -> list[GoldenExample]:
	return [
		GoldenExample.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


def gate_misses(examples: list[GoldenExample]) -> list[str]:
	"""Cases with a prayer in them that the app would never read, because none of the
	prayer words appear. Must be empty: the gate trades cost, not recall."""
	return [e.id for e in examples if e.expected and not might_pray(e.text)]


async def run(
	examples: list[GoldenExample],
	reader: FaithReaderLike | None,
	replay: dict[str, str] | None,
	record_to: Path | None,
) -> Report:
	report = Report()
	recorded: dict[str, str] = {}
	for example in examples:
		if replay is not None:
			raw = replay.get(example.id)
			try:
				predicted = FaithExtraction.model_validate_json(raw or "{}")
			except ValidationError:
				predicted = (
					FaithExtraction()
				)  # a recorded failure keeps its raw: a miss
		else:
			assert reader is not None
			try:
				result = await reader.read(
					example.text, captured_at=CAPTURED_AT, timezone=TIMEZONE
				)
				predicted, recorded[example.id] = result.extraction, result.raw
			except ExtractionFailed as exc:
				predicted, recorded[example.id] = FaithExtraction(), exc.raw
			except TerminalError as exc:
				print(f"{example.id}: {exc}", file=sys.stderr)
				predicted, recorded[example.id] = FaithExtraction(), ""
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
	reader: FaithReaderLike | None = None
	if replay is None:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		reader = FaithReader(
			AnthropicExtractionProvider(
				client,
				model=args.model or settings.faith_model,
				schema=FaithExtraction,
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
		failures.append(f"the prayer-word gate would skip {', '.join(skipped)}")
	if failures:
		print("BELOW THRESHOLD:", "; ".join(failures))
		return 1
	print("all thresholds met")
	return 0


if __name__ == "__main__":
	sys.exit(main())
