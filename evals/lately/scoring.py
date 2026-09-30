"""Scoring Lately (ADR 0062): does the line say what's going on, only what the notes
say, and nothing when they say nothing?

The errors that cost trust get bars of 1.00: a name the notes never mention, a "he" or
"she" the notes never used, a fact the newer notes replaced, a line past the length
the page has room for. Recall of the key fact is lower: a line that picks the second
most important thing is fine, and a person's page still has the notes below it.
"""

import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from memo.prompts.lately_v1 import MAX_CHARS

THRESHOLDS: dict[str, float] = {
	"key_facts": 0.85,
	"speaks_when_it_should": 0.90,
	"no_invented_names": 1.00,
	"no_guessed_pronouns": 1.00,
	"no_stale_facts": 1.00,
	"fits": 1.00,
}

_CAPITALISED = re.compile(r"\b[A-Z][a-z]+\b")
_CALENDAR = {
	"Monday",
	"Tuesday",
	"Wednesday",
	"Thursday",
	"Friday",
	"Saturday",
	"Sunday",
	"January",
	"February",
	"March",
	"April",
	"May",
	"June",
	"July",
	"August",
	"September",
	"October",
	"November",
	"December",
	"You",
	"Your",
	"Lately",
	"God",
}
_PRONOUNS = re.compile(r"\b(he|she|him|her|his|hers)\b", re.IGNORECASE)


class Expected(BaseModel):
	must: list[str] = []  # each "a|b": one of them should appear
	must_not: list[str] = []
	empty: bool = False


@dataclass
class Counts:
	facts: int = 0
	facts_hit: int = 0
	spoke_right: int = 0
	cases: int = 0
	invented: int = 0
	pronouns: int = 0
	stale: int = 0
	too_long: int = 0

	def add(self, other: "Counts") -> None:
		for name in self.__dataclass_fields__:
			setattr(self, name, getattr(self, name) + getattr(other, name))

	def metrics(self) -> dict[str, float]:
		n = self.cases or 1
		return {
			"key_facts": self.facts_hit / self.facts if self.facts else 1.0,
			"speaks_when_it_should": self.spoke_right / n,
			"no_invented_names": 1 - self.invented / n,
			"no_guessed_pronouns": 1 - self.pronouns / n,
			"no_stale_facts": 1 - self.stale / n,
			"fits": 1 - self.too_long / n,
		}


def score_example(source: str, expected: Expected, line: str) -> Counts:
	"""`source` is everything the writer was given, for checking what it may say."""
	c = Counts(cases=1)
	said = line.casefold()
	c.spoke_right = int(bool(line.strip()) != expected.empty)
	for group in expected.must:
		c.facts += 1
		c.facts_hit += int(any(w in said for w in group.split("|")))
	# A capital that starts a sentence is grammar, not a name.
	opening = {m.group(1) for m in re.finditer(r"(?:^|[.!?]\s+)([A-Z][a-z]+)", line)}
	names = {w for w in _CAPITALISED.findall(line) if w not in _CALENDAR | opening}
	c.invented = int(any(w.casefold() not in source.casefold() for w in names))
	c.pronouns = int(bool(_PRONOUNS.search(line)) and not _PRONOUNS.search(source))
	c.stale = int(any(w.casefold() in said for w in expected.must_not))
	c.too_long = int(len(line) > MAX_CHARS)
	return c


@dataclass
class Report:
	totals: Counts = field(default_factory=Counts)
	examples: int = 0
	misses: list[str] = field(default_factory=list)

	def add(self, example_id: str, counts: Counts, line: str) -> None:
		self.examples += 1
		self.totals.add(counts)
		wrong = {
			"missed a key fact": counts.facts - counts.facts_hit,
			"spoke when it shouldn't, or didn't when it should": 1 - counts.spoke_right,
			"a name the notes don't have": counts.invented,
			"a guessed pronoun": counts.pronouns,
			"a replaced fact": counts.stale,
			"too long": counts.too_long,
		}
		for what, n in wrong.items():
			if n:
				self.misses.append(f"{example_id}: {what} ({line!r})")

	def failures(self) -> list[str]:
		metrics = self.totals.metrics()
		return [
			f"{name} {metrics[name]:.2f} < {bar:.2f}"
			for name, bar in THRESHOLDS.items()
			if metrics[name] < bar
		]

	def table(self) -> str:
		return "\n".join(
			f"{name:<22} {value:>5.2f}  (bar {THRESHOLDS[name]:.2f})"
			for name, value in self.totals.metrics().items()
		)
