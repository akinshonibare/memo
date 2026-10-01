"""Run the habits-reader eval over the golden set and gate on the thresholds.

	uv run python -m evals.habits.run              # live; needs ANTHROPIC_API_KEY
	uv run python -m evals.habits.run --record evals/habits/recorded.json
	uv run python -m evals.habits.run --replay evals/habits/recorded.json  # CI

Exit code 1 when any metric is below its bar - the merge gate, as for faith.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from evals.habits.scoring import Expected, Report, score_example
from memo.config import get_settings
from memo.schemas.habits import HabitsExtraction
from memo.services.extraction import AnthropicExtractionProvider, ExtractionFailed
from memo.services.habits import HabitsReader, HabitsReaderLike
from memo.services.retry import RetryableError, TerminalError

GOLDEN = Path(__file__).with_name("golden.jsonl")
CAPTURED_AT = dt.datetime.fromisoformat("2026-09-28T09:00:00+01:00")  # a Monday
TIMEZONE = "Europe/London"


class GoldenExample(BaseModel):
	id: str
	text: str
	expected: list[Expected]
	# Habits the author named themselves, given to the reader as in the app.
	extra_keys: list[str] = []
	note: str | None = None


def load_golden(path: Path = GOLDEN) -> list[GoldenExample]:
	return [
		GoldenExample.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


async def _read(
	reader: HabitsReaderLike, example: GoldenExample
) -> tuple[HabitsExtraction, str]:
	# A 429 or 503 is the provider, not the prompt: wait and ask again, a few times.
	for attempt in range(4):
		try:
			result = await reader.read(
				example.text,
				captured_at=CAPTURED_AT,
				timezone=TIMEZONE,
				extra_keys=example.extra_keys or None,
			)
			return result.extraction, result.raw
		except RetryableError:
			if attempt == 3:
				raise
			await asyncio.sleep(5 * (attempt + 1))
	raise AssertionError("unreachable")


def _parse(raw: str | None) -> HabitsExtraction:
	# Recorded outputs are replayed through the same validation as the app, with the
	# note's own date, so a future-dated activity is dropped here as it would be there.
	try:
		return HabitsExtraction.model_validate_json(
			raw or "{}", context={"captured_on": CAPTURED_AT.date()}
		)
	except ValidationError:
		return HabitsExtraction()  # a recorded failure keeps its raw: a miss


async def run(
	examples: list[GoldenExample],
	reader: HabitsReaderLike | None,
	replay: dict[str, str] | None,
	record_to: Path | None,
) -> Report:
	report = Report()
	recorded: dict[str, str] = {}
	for example in examples:
		if replay is not None:
			predicted = _parse(replay.get(example.id))
		else:
			assert reader is not None
			try:
				predicted, recorded[example.id] = await _read(reader, example)
			except ExtractionFailed as exc:
				predicted, recorded[example.id] = HabitsExtraction(), exc.raw
			except TerminalError as exc:
				print(f"{example.id}: {exc}", file=sys.stderr)
				predicted, recorded[example.id] = HabitsExtraction(), ""
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
	reader: HabitsReaderLike | None = None
	if replay is None:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		reader = HabitsReader(
			AnthropicExtractionProvider(
				client,
				model=args.model or settings.habits_model,
				schema=HabitsExtraction,
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
