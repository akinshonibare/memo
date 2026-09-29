"""Scoring the people reader (ADR 0029): who, whether they were in touch, and when.

The costs are not symmetric, so neither are the bars. A person the reader misses is
a note missing from their page. A mention the reader calls *contact* when it was not
("need to call Tunde", "Ada says Tunde got the job") is worse: it moves Tunde's last
contact forward, so the reminder that should have come never does, and nothing looks
wrong. Contact precision therefore has the highest bar here.
"""

import datetime as dt
from dataclasses import dataclass, field

from pydantic import BaseModel

from memo.schemas.people import IN_TOUCH, PeopleExtraction
from memo.services.people import compatible

THRESHOLDS: dict[str, float] = {
	"person_recall": 0.85,
	"person_precision": 0.85,
	"contact_precision": 0.95,
	"contact_recall": 0.85,
	"date_accuracy": 0.85,
}


class Expected(BaseModel):
	name: str
	contact: str
	occurred_on: dt.date | None = None


@dataclass
class Counts:
	person_tp: int = 0
	person_fp: int = 0
	person_fn: int = 0
	contact_tp: int = 0  # in touch, and said so
	contact_fp: int = 0  # said in touch, was not: the silent failure
	contact_fn: int = 0  # in touch, said not
	dates_right: int = 0
	dates_total: int = 0

	def add(self, other: "Counts") -> None:
		for name in self.__dataclass_fields__:
			setattr(self, name, getattr(self, name) + getattr(other, name))

	@staticmethod
	def _ratio(hit: int, miss: int) -> float:
		return hit / (hit + miss) if hit + miss else 1.0

	def metrics(self) -> dict[str, float]:
		return {
			"person_recall": self._ratio(self.person_tp, self.person_fn),
			"person_precision": self._ratio(self.person_tp, self.person_fp),
			"contact_precision": self._ratio(self.contact_tp, self.contact_fp),
			"contact_recall": self._ratio(self.contact_tp, self.contact_fn),
			"date_accuracy": self._ratio(
				self.dates_right, self.dates_total - self.dates_right
			),
		}


def score_example(expected: list[Expected], predicted: PeopleExtraction) -> Counts:
	"""Pair people by name (the resolver's own `compatible`, so "my brother" meets
	"brother"), then compare contact and date on each pair."""
	counts = Counts()
	remaining = list(expected)
	for person in predicted.people:
		names = [n for n in (person.full_name, person.name) if n]
		hit = next(
			(e for e in remaining if any(compatible(e.name, n) for n in names)), None
		)
		if hit is None:
			counts.person_fp += 1
			continue
		remaining.remove(hit)
		counts.person_tp += 1
		said, was = person.contact in IN_TOUCH, hit.contact in IN_TOUCH
		if said and was:
			counts.contact_tp += 1
			counts.dates_total += 1
			counts.dates_right += int(person.occurred_on == hit.occurred_on)
		elif said:
			counts.contact_fp += 1
		elif was:
			counts.contact_fn += 1
	counts.person_fn += len(remaining)
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
			"missed": counts.person_fn,
			"extra": counts.person_fp,
			"false contact": counts.contact_fp,
			"missed contact": counts.contact_fn,
			"wrong date": counts.dates_total - counts.dates_right,
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
			f"{name:<18} {value:>5.2f}  (bar {THRESHOLDS[name]:.2f})"
			for name, value in self.totals.metrics().items()
		)
