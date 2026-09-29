"""Scoring the habits reader (ADR 0043): does it find what the author actually did, on
the day they did it, and nothing they only planned?

Scored the way the app counts: one day per (key, date), however many times the note says
it. The costly error is a false count - "gym tomorrow" filed as a gym day - because
inflated numbers look fine and nobody checks them. So precision has the highest bar. A
missed day is visible (the habit looks quieter than the week felt) and one tap to add.
A wrong date moves a day between weeks, which is the week-by-week view going wrong.
"""

import datetime as dt
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from memo.schemas.habits import HabitsExtraction

THRESHOLDS: dict[str, float] = {
	"activity_precision": 0.95,
	"activity_recall": 0.85,
	"date_accuracy": 0.90,
}


class Expected(BaseModel):
	key: str
	on: dt.date
	# Other keys that are also right ("five-a-side" is sport; football would do too).
	or_: list[str] = Field(default_factory=list, alias="or")

	def keys(self) -> set[str]:
		return {self.key, *self.or_}


@dataclass
class Counts:
	tp: int = 0
	fp: int = 0
	fn: int = 0
	dates_right: int = 0

	def add(self, other: "Counts") -> None:
		for name in self.__dataclass_fields__:
			setattr(self, name, getattr(self, name) + getattr(other, name))

	@staticmethod
	def _ratio(hit: int, miss: int) -> float:
		return hit / (hit + miss) if hit + miss else 1.0

	def metrics(self) -> dict[str, float]:
		return {
			"activity_precision": self._ratio(self.tp, self.fp),
			"activity_recall": self._ratio(self.tp, self.fn),
			"date_accuracy": self._ratio(self.dates_right, self.tp - self.dates_right),
		}


def score_example(expected: list[Expected], predicted: HabitsExtraction) -> Counts:
	counts = Counts()
	days = sorted({(a.key, a.occurred_on) for a in predicted.activities})
	remaining = list(expected)
	# Exact days first, so "gym Monday and Wednesday" pairs each day with its own.
	unmatched = []
	for key, on in days:
		hit = next((e for e in remaining if key in e.keys() and e.on == on), None)
		if hit is None:
			unmatched.append((key, on))
			continue
		remaining.remove(hit)
		counts.tp += 1
		counts.dates_right += 1
	for key, _ in unmatched:
		hit = next((e for e in remaining if key in e.keys()), None)
		if hit is None:
			counts.fp += 1
			continue
		remaining.remove(hit)
		counts.tp += 1  # the right activity, on the wrong day
	counts.fn += len(remaining)
	return counts


@dataclass
class Report:
	totals: Counts = field(default_factory=Counts)
	examples: int = 0
	misses: list[str] = field(default_factory=list)

	def add(self, example_id: str, counts: Counts) -> None:
		self.examples += 1
		self.totals.add(counts)
		wrong = {
			"missed": counts.fn,
			"extra": counts.fp,
			"wrong day": counts.tp - counts.dates_right,
		}
		for what, n in wrong.items():
			if n:
				self.misses.append(f"{example_id}: {what} x{n}")

	def failures(self) -> list[str]:
		metrics = self.totals.metrics()
		return [
			f"{name} {metrics[name]:.2f} < {bar:.2f}"
			for name, bar in THRESHOLDS.items()
			if metrics[name] < bar
		]

	def table(self) -> str:
		return "\n".join(
			f"{name:<19} {value:>5.2f}  (bar {THRESHOLDS[name]:.2f})"
			for name, value in self.totals.metrics().items()
		)
