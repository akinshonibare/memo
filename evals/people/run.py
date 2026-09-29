"""Run the people-reader eval over the golden set and gate on the thresholds.

	uv run python -m evals.people.run              # live; needs ANTHROPIC_API_KEY
	uv run python -m evals.people.run --record evals/people/recorded.json
	uv run python -m evals.people.run --replay evals/people/recorded.json  # CI

Exit code 1 when any metric is below its bar - the merge gate, as for extraction.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from evals.people.scoring import Expected, Report, score_example
from memo.config import get_settings
from memo.schemas.people import PeopleExtraction
from memo.services.extraction import AnthropicExtractionProvider, ExtractionFailed
from memo.services.people import PeopleReader, PeopleReaderLike
from memo.services.retry import TerminalError

GOLDEN = Path(__file__).with_name("golden.jsonl")
# Every case is read as written on this Monday morning in London, by "Akin".
CAPTURED_AT = dt.datetime.fromisoformat("2026-09-28T09:00:00+01:00")
TIMEZONE = "Europe/London"
AUTHOR = "Akin"


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


async def run(
	examples: list[GoldenExample],
	reader: PeopleReaderLike | None,
	replay: dict[str, str] | None,
	record_to: Path | None,
) -> Report:
	report = Report()
	recorded: dict[str, str] = {}
	for example in examples:
		if replay is not None:
			raw = replay.get(example.id)
			try:
				predicted = PeopleExtraction.model_validate_json(raw or "{}")
			except ValidationError:
				# A recorded ExtractionFailed keeps the invalid raw output: a miss.
				predicted = PeopleExtraction()
		else:
			assert reader is not None
			try:
				result = await reader.read(
					example.text,
					captured_at=CAPTURED_AT,
					timezone=TIMEZONE,
					author=AUTHOR,
				)
				predicted, recorded[example.id] = result.extraction, result.raw
			except ExtractionFailed as exc:
				predicted, recorded[example.id] = PeopleExtraction(), exc.raw
			except TerminalError as exc:
				print(f"{example.id}: {exc}", file=sys.stderr)
				predicted, recorded[example.id] = PeopleExtraction(), ""
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
	reader: PeopleReaderLike | None = None
	if replay is None:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		reader = PeopleReader(
			AnthropicExtractionProvider(
				client,
				model=args.model or settings.extraction_model,
				schema=PeopleExtraction,
			)
		)
	report = asyncio.run(run(examples, reader, replay, args.record))
	print(f"{report.examples} examples")
	print(report.table())
	for miss in report.misses:
		print("  miss:", miss)
	failures = report.failures()
	if failures:
		print("BELOW THRESHOLD:", "; ".join(failures))
		return 1
	print("all thresholds met")
	return 0


if __name__ == "__main__":
	sys.exit(main())
