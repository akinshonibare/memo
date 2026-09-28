"""Run the extraction eval over the golden set and gate on the thresholds.

	uv run python -m evals.extraction.run              # live; needs ANTHROPIC_API_KEY
	uv run python -m evals.extraction.run --record out.json
	uv run python -m evals.extraction.run --replay out.json   # no network (CI)

Exit code 1 when any kind is below its threshold - that is the merge gate (#25).
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from evals.extraction.scoring import Report, score_example
from memo.config import get_settings
from memo.schemas.derivations import Extraction
from memo.services.extraction import (
	AnthropicExtractionProvider,
	ExtractionFailed,
	Extractor,
	ExtractorLike,
)

GOLDEN = Path(__file__).with_name("golden.jsonl")


class GoldenExample(BaseModel):
	id: str
	text: str
	captured_at: dt.datetime
	timezone: str = "UTC"
	expected: Extraction
	note: str | None = None


def load_golden(path: Path = GOLDEN) -> list[GoldenExample]:
	return [
		GoldenExample.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


async def run(
	examples: list[GoldenExample],
	extractor: ExtractorLike | None,
	replay: dict[str, str] | None,
	record_to: Path | None,
) -> Report:
	report = Report()
	recorded: dict[str, str] = {}
	for example in examples:
		if replay is not None:
			raw = replay.get(example.id)
			predicted = Extraction.model_validate_json(raw) if raw else Extraction()
		else:
			assert extractor is not None
			try:
				result = await extractor.extract(
					example.text,
					captured_at=example.captured_at,
					timezone=example.timezone,
				)
				predicted, recorded[example.id] = result.extraction, result.raw
			except ExtractionFailed as exc:
				predicted, recorded[example.id] = Extraction(), exc.raw
		report.add(example.id, score_example(example.expected, predicted))
	if record_to is not None:
		record_to.write_text(json.dumps(recorded, indent=2))
	return report


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--replay", type=Path, help="JSON of {id: raw output}")
	parser.add_argument("--record", type=Path, help="write raw outputs here")
	parser.add_argument("--model", default=None)
	args = parser.parse_args(argv)

	examples = load_golden()
	replay = json.loads(args.replay.read_text()) if args.replay else None
	extractor: ExtractorLike | None = None
	if replay is None:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		extractor = Extractor(
			AnthropicExtractionProvider(
				client, model=args.model or settings.extraction_model
			)
		)

	report = asyncio.run(run(examples, extractor, replay, args.record))
	print(f"{report.examples} examples\n{report.table()}")
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
