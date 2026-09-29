"""Scoring the habits reader (ADR 0043): does it find what the author actually did, on
the day they did it, and nothing they only planned?

Scored the way the app counts: one day per (key, date), however many times the note says
it. The costly error is a false count - "gym tomorrow" filed as a gym day - because
inflated numbers look fine and nobody checks them. So precision has the highest bar. A
missed day is visible (the habit looks quieter than the week felt) and one tap to add.
A wrong date moves a day between weeks, which is the week-by-week view going wrong.

Since habits-v2 (ADR 0050) an activity carries the numbers the note gave - how far, how
long, how heavy - and milestones are read from them. An invented number ("a long run"
filed as 15 km) is a false "Longest run yet", so amount precision has the high bar too.
Amounts are scored on the activities that matched, in memo's own units.
"""

import datetime as dt
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from memo.schemas.habits import HabitsExtraction

THRESHOLDS: dict[str, float] = {
	"activity_precision": 0.95,
	"activity_recall": 0.85,
	"date_accuracy": 0.90,
	"amount_precision": 0.95,
	"amount_recall": 0.80,
}
# A number read back within this of the truth is right: 3 miles is 4.83 km, not 4.8.
AMOUNT_TOLERANCE = 0.02


class ExpectedAmount(BaseModel):
	measure: str
	value: float  # in km, minutes or kg
	lift: str = ""


class Expected(BaseModel):
	key: str
	on: dt.date
	# Other keys that are also right ("five-a-side" is sport; football would do too).
	or_: list[str] = Field(default_factory=list, alias="or")
	# The numbers the note gives for it. None when the note is ambiguous about what a
	# number belongs to, and it isn't scored.
	amounts: list[ExpectedAmount] | None = Field(default_factory=list)

	def keys(self) -> set[str]:
		return {self.key, *self.or_}


@dataclass
class Counts:
	tp: int = 0
	fp: int = 0
	fn: int = 0
	dates_right: int = 0
	amount_tp: int = 0
	amount_fp: int = 0
	amount_fn: int = 0

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
			"amount_precision": self._ratio(self.amount_tp, self.amount_fp),
			"amount_recall": self._ratio(self.amount_tp, self.amount_fn),
		}


Amount = tuple[str, float, str]  # measure, value in memo's units, lift


def _amounts(predicted: HabitsExtraction) -> dict[tuple[str, dt.date], list[Amount]]:
	out: dict[tuple[str, dt.date], list[Amount]] = {}
	for a in predicted.activities:
		out.setdefault((a.key, a.occurred_on), []).extend(
			(x.measure, x.base, x.lift) for x in a.amounts
		)
	return out


def _score_amounts(counts: "Counts", expected: Expected, got: list[Amount]) -> None:
	if expected.amounts is None:
		return
	remaining = [(e.measure, e.value, e.lift) for e in expected.amounts]
	for measure, value, lift in got:
		hit = next(
			(
				e
				for e in remaining
				if e[0] == measure
				and e[2] == lift
				and abs(e[1] - value) <= AMOUNT_TOLERANCE * e[1]
			),
			None,
		)
		if hit is None:
			counts.amount_fp += 1
		else:
			remaining.remove(hit)
			counts.amount_tp += 1
	counts.amount_fn += len(remaining)


def score_example(expected: list[Expected], predicted: HabitsExtraction) -> Counts:
	counts = Counts()
	amounts = _amounts(predicted)
	days = sorted(amounts)
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
		_score_amounts(counts, hit, amounts[(key, on)])
	for key, on in unmatched:
		hit = next((e for e in remaining if key in e.keys()), None)
		if hit is None:
			counts.fp += 1
			continue
		remaining.remove(hit)
		counts.tp += 1  # the right activity, on the wrong day
		_score_amounts(counts, hit, amounts[(key, on)])
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
			"extra amount": counts.amount_fp,
			"missed amount": counts.amount_fn,
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
