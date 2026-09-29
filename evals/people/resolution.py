"""The resolver eval (ADR 0029): which person a name means, over labelled cases.

	uv run python -m evals.people.resolution

Deterministic, so it runs in CI with no recording. Every case must come out as
labelled, and the report says which kind of mistake a failure is, because they cost
different amounts:

- **wrong link**: linked to the wrong person, or linked when it should have asked.
  Two people's histories silently merged. The error this resolver exists to avoid.
- **split**: made someone new who already existed. One merge fixes it.
- **needless question**: asked when the answer was clear. One tap.

Real cases belong here: every "which one?" you answer in the app is a label.
"""

import sys
import uuid
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from memo.config import SEED_USER_ID
from memo.models import Person
from memo.schemas.people import MentionPayload
from memo.services.people import resolve

CASES = Path(__file__).with_name("resolution.jsonl")


class KnownPerson(BaseModel):
	name: str
	aliases: list[str] = []


class ExpectedResolution(BaseModel):
	outcome: Literal["link", "ask", "new"]
	person: str | None = None
	candidates: list[str] = []


class Case(BaseModel):
	id: str
	people: list[KnownPerson]
	mention: MentionPayload
	expected: ExpectedResolution
	note: str | None = None


def load_cases(path: Path = CASES) -> list[Case]:
	return [
		Case.model_validate_json(line)
		for line in path.read_text().splitlines()
		if line.strip()
	]


def judge(case: Case) -> str | None:
	"""None when the case comes out as labelled, else what kind of mistake it is."""
	people = [
		Person(id=uuid.uuid4(), user_id=SEED_USER_ID, name=p.name, aliases=p.aliases)
		for p in case.people
	]
	names = {p.id: p.name for p in people}
	got = resolve(case.mention, people)
	want = case.expected
	if got.outcome == "link":
		linked = names[got.person_id] if got.person_id else None
		if want.outcome != "link" or linked != want.person:
			return (
				f"wrong link (to {linked}; wanted {want.outcome} {want.person or ''})"
			)
		return None
	if want.outcome == "link":
		return "split" if got.outcome == "new" else "needless question"
	if got.outcome != want.outcome:
		return f"{got.outcome} (wanted {want.outcome})"
	asked = sorted(names[c] for c in got.candidates)
	if got.outcome == "ask" and asked != sorted(want.candidates):
		return f"asked about {asked} (wanted {sorted(want.candidates)})"
	return None


def main() -> int:
	cases = load_cases()
	failures = [(c.id, why) for c in cases if (why := judge(c)) is not None]
	wrong_links = sum(1 for _, why in failures if why.startswith("wrong link"))
	print(f"{len(cases)} cases, {len(failures)} off, {wrong_links} wrong links")
	for case_id, why in failures:
		print(f"  {case_id}: {why}")
	return 1 if failures else 0


if __name__ == "__main__":
	sys.exit(main())
