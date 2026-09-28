"""Faithfulness eval (issue #37): is the judge right, and are the answers faithful?

	uv run python -m evals.faithfulness.run            # heuristic judge, offline
	uv run python -m evals.faithfulness.run --llm      # Claude as judge; needs a key
	uv run python -m evals.faithfulness.run --answers a.json   # judge {id: answer}

Without --answers, the case file's own answers are judged and the verdicts are compared
with expected_faithful: that calibrates the judge. With --answers (produced by the real
ask pipeline), the gate is the faithful rate itself.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from anthropic import AsyncAnthropic
from pydantic import BaseModel

from evals.faithfulness.judge import HeuristicJudge, Judge, LLMJudge
from memo.config import get_settings

CASES = Path(__file__).with_name("cases.jsonl")
JUDGE_ACCURACY_BAR = 0.9
FAITHFUL_RATE_BAR = 0.9


class Case(BaseModel):
	id: str
	question: str
	cited: list[str]
	answer: str
	expected_faithful: bool
	note: str | None = None


def load_cases(path: Path = CASES) -> list[Case]:
	return [
		Case.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


async def calibrate(judge: Judge, cases: list[Case]) -> tuple[float, list[str]]:
	wrong = []
	for case in cases:
		verdict = await judge.judge(answer=case.answer, cited=case.cited)
		if verdict.faithful != case.expected_faithful:
			wrong.append(
				f"{case.id}: expected {case.expected_faithful}, got {verdict.faithful} "
				f"(unsupported: {verdict.unsupported})"
			)
	return 1 - len(wrong) / len(cases), wrong


async def score_answers(
	judge: Judge, cases: list[Case], answers: dict[str, str]
) -> tuple[float, list[str]]:
	unfaithful = []
	scored = 0
	for case in cases:
		answer = answers.get(case.id)
		if answer is None:
			continue
		scored += 1
		verdict = await judge.judge(answer=answer, cited=case.cited)
		if not verdict.faithful:
			unfaithful.append(f"{case.id}: {verdict.unsupported}")
	return (1 - len(unfaithful) / scored) if scored else 1.0, unfaithful


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("--llm", action="store_true")
	parser.add_argument("--answers", type=Path)
	args = parser.parse_args(argv)

	judge: Judge
	if args.llm:
		settings = get_settings()
		if settings.anthropic_api_key is None:
			print("ANTHROPIC_API_KEY is unset; drop --llm for the heuristic judge")
			return 2
		judge = LLMJudge(
			AsyncAnthropic(api_key=settings.anthropic_api_key.get_secret_value())
		)
	else:
		judge = HeuristicJudge()

	cases = load_cases()
	if args.answers:
		rate, unfaithful = asyncio.run(
			score_answers(judge, cases, json.loads(args.answers.read_text()))
		)
		print(f"faithful rate {rate:.2f} over {len(cases)} cases")
		for line in unfaithful:
			print("  unfaithful:", line)
		if rate < FAITHFUL_RATE_BAR:
			print(f"BELOW THRESHOLD: {rate:.2f} < {FAITHFUL_RATE_BAR}")
			return 1
		return 0

	accuracy, wrong = asyncio.run(calibrate(judge, cases))
	print(f"judge accuracy {accuracy:.2f} over {len(cases)} calibration cases")
	for line in wrong:
		print("  wrong:", line)
	if accuracy < JUDGE_ACCURACY_BAR:
		print(f"BELOW THRESHOLD: {accuracy:.2f} < {JUDGE_ACCURACY_BAR}")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
