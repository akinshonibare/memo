"""Run the Lately eval over the golden set and gate on the thresholds.

	uv run python -m evals.lately.run              # live; needs ANTHROPIC_API_KEY
	uv run python -m evals.lately.run --record evals/lately/recorded.json
	uv run python -m evals.lately.run --replay evals/lately/recorded.json  # CI

Every case is written at the default tone (plain), like the other evals.
Exit code 1 when any metric is below its bar: the merge gate.
"""

import argparse
import asyncio
import datetime as dt
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from evals.lately.scoring import Expected, Report, score_example
from memo.config import get_settings
from memo.prompts.lately_v1 import Mentioned, user_message
from memo.schemas.people import LatelyWrite
from memo.services.extraction import AnthropicExtractionProvider, ExtractionFailed
from memo.services.lately import LatelyWriter, LatelyWriterLike
from memo.services.prose import plain_dashes
from memo.services.retry import TerminalError

GOLDEN = Path(__file__).with_name("golden.jsonl")
TODAY = dt.date(2026, 9, 30)


class Note(BaseModel):
	on: dt.date
	in_touch: str | None = None
	about: str | None = None
	opening: str | None = None


class GoldenExample(BaseModel):
	id: str
	name: str
	notes: list[Note]
	must: list[str] = []
	must_not: list[str] = []
	empty: bool = False
	note: str | None = None

	def mentioned(self) -> list[Mentioned]:
		return [Mentioned(n.on, n.in_touch, n.about, n.opening) for n in self.notes]

	def expected(self) -> Expected:
		return Expected(must=self.must, must_not=self.must_not, empty=self.empty)


def load_golden(path: Path = GOLDEN) -> list[GoldenExample]:
	return [
		GoldenExample.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


async def run(
	examples: list[GoldenExample],
	writer: LatelyWriterLike | None,
	replay: dict[str, str] | None,
	record_to: Path | None,
) -> Report:
	report = Report()
	recorded: dict[str, str] = {}
	for example in examples:
		if replay is not None:
			try:
				written = LatelyWrite.model_validate_json(
					replay.get(example.id) or "{}"
				)
			except ValidationError:
				written = LatelyWrite()
		else:
			assert writer is not None
			try:
				result = await writer.write(
					example.name, TODAY, example.mentioned(), tone="plain"
				)
				written, recorded[example.id] = result.written, result.raw
			except ExtractionFailed as exc:
				written, recorded[example.id] = LatelyWrite(), exc.raw
			except TerminalError as exc:
				print(f"{example.id}: {exc}", file=sys.stderr)
				written, recorded[example.id] = LatelyWrite(), ""
		line = plain_dashes(written.text)
		source = user_message(example.name, TODAY, example.mentioned())
		report.add(example.id, score_example(source, example.expected(), line), line)
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
	writer: LatelyWriterLike | None = None
	if replay is None:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		writer = LatelyWriter(
			AnthropicExtractionProvider(
				client,
				model=args.model or settings.extraction_model,
				schema=LatelyWrite,
			)
		)
	report = asyncio.run(run(examples, writer, replay, args.record))
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
