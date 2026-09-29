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
from typing import Any

from anthropic import AsyncAnthropic
from pydantic import BaseModel, ValidationError

from evals.extraction.matching import (
	LLMMatcher,
	dump_matches,
	fingerprint,
	load_matches,
)
from evals.extraction.scoring import Report, score_example
from memo.config import get_settings
from memo.schemas.derivations import Extraction
from memo.services.extraction import (
	AnthropicExtractionProvider,
	ExtractionFailed,
	Extractor,
	ExtractorLike,
)
from memo.services.retry import TerminalError

GOLDEN = Path(__file__).with_name("golden.jsonl")
MATCHES = Path(__file__).with_name("matches.json")


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
	matches: dict[str, dict[str, Any]] | None = None,
	matcher: LLMMatcher | None = None,
) -> Report:
	"""`matches` holds recorded semantic verdicts, used where their fingerprint still
	fits; `matcher` (with a key) judges afresh and updates `matches` in place."""
	report = Report()
	recorded: dict[str, str] = {}
	matches = matches if matches is not None else {}
	for example in examples:
		if replay is not None:
			raw = replay.get(example.id)
			try:
				predicted = Extraction.model_validate_json(raw or "{}")
			except ValidationError:
				# A recorded ExtractionFailed keeps the invalid raw output: a miss.
				predicted = Extraction()
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
			except TerminalError as exc:
				# A truncation or refusal is a miss to be scored, not a crash: the
				# gate exists to measure exactly these (a runaway output once took
				# g05 to max_tokens on every other run).
				print(f"{example.id}: {exc}", file=sys.stderr)
				predicted, recorded[example.id] = Extraction(), ""
		pairs = None
		fp = fingerprint(example.expected, predicted)
		if matcher is not None:
			judged = await matcher.match(example.expected, predicted)
			matches[example.id] = {
				"fingerprint": fp,
				"pairs": {k: [list(p) for p in v] for k, v in judged.items()},
			}
		entry = matches.get(example.id)
		if entry is not None and entry.get("fingerprint") == fp:
			pairs = {k: [(e, p) for e, p in v] for k, v in entry["pairs"].items()}
			report.semantic += 1
		report.add(example.id, score_example(example.expected, predicted, pairs))
	if record_to is not None:
		record_to.write_text(json.dumps(recorded, indent=2))
	return report


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--replay", type=Path, help="JSON of {id: raw output}")
	parser.add_argument("--record", type=Path, help="write raw outputs here")
	parser.add_argument("--model", default=None)
	parser.add_argument(
		"--match",
		action="store_true",
		help=f"judge item matches with a model and record them to {MATCHES.name}",
	)
	args = parser.parse_args(argv)

	examples = load_golden()
	replay = json.loads(args.replay.read_text()) if args.replay else None
	extractor: ExtractorLike | None = None
	matcher: LLMMatcher | None = None
	if replay is None or args.match:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; use --replay for an offline run")
			return 2
		client = AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		if replay is None:
			extractor = Extractor(
				AnthropicExtractionProvider(
					client, model=args.model or settings.extraction_model
				)
			)
		if args.match:
			matcher = LLMMatcher(client)

	matches = load_matches(
		json.loads(MATCHES.read_text()) if MATCHES.exists() and not args.match else None
	)
	report = asyncio.run(
		run(examples, extractor, replay, args.record, matches, matcher)
	)
	if args.match:
		MATCHES.write_text(dump_matches(matches) + "\n")
	print(f"{report.examples} examples ({report.semantic} scored by recorded matches)")
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
