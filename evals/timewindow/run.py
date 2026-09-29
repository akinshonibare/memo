"""The time-window eval (ADR 0044): how many time questions get the right days.

	uv run python -m evals.timewindow.run

Deterministic, so it runs in CI with no recording. Every case is asked on Wednesday
30 September 2026. A case is right when the parser gives exactly the labelled days, or
gives none when none is labelled. Two ways to be wrong, and they cost different
amounts:

- **miss**: a time the parser didn't read. Ask falls back to plain retrieval, which is
  how it behaved before ADR 0044: not worse, just not better.
- **wrong days**: a window that isn't what was meant. Ask lists the wrong items first
  and searches the wrong days. This is the one that hurts, and the gate allows none.

Real questions belong here: `ask.time_unparsed` in the logs names the word each missed
question used. The gate is the trigger in ADR 0044 for replacing this parser with a
model-parsed filter.
"""

import datetime as dt
import sys
from pathlib import Path

from pydantic import BaseModel

from memo.services.timewindow import parse

CASES = Path(__file__).with_name("cases.jsonl")
TODAY = dt.date(2026, 9, 30)
ACCURACY_BAR = 0.85


class Case(BaseModel):
	id: str
	question: str
	expected: tuple[dt.date, dt.date] | None
	note: str | None = None


def load_cases(path: Path = CASES) -> list[Case]:
	return [
		Case.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


def judge(case: Case) -> str | None:
	"""None when right, else "miss" or "wrong days"."""
	window = parse(case.question, TODAY)
	got = (window.start, window.end) if window else None
	if got == case.expected:
		return None
	return "miss" if got is None else "wrong days"


def main() -> int:
	cases = load_cases()
	results = {case.id: judge(case) for case in cases}
	right = sum(1 for r in results.values() if r is None)
	wrong_days = [c for c in cases if results[c.id] == "wrong days"]
	for case in cases:
		if results[case.id]:
			print(
				f"{case.id} {results[case.id]}: {case.question!r} ({case.note or ''})"
			)
	accuracy = right / len(cases)
	print(f"time windows: {right}/{len(cases)} right ({accuracy:.2f})")
	if wrong_days:
		print(f"FAIL: {len(wrong_days)} wrong window(s); the gate allows none")
		return 1
	if accuracy < ACCURACY_BAR:
		print(f"FAIL: accuracy {accuracy:.2f} < {ACCURACY_BAR}")
		return 1
	return 0


if __name__ == "__main__":
	sys.exit(main())
