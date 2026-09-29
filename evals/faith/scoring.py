"""Scoring the faith reader (ADR 0042): does it find what the author is praying for, and
only that?

A missed prayer is a prayer that never reaches Faith: the author adds it by hand, or it
is lost. An invented one - "oh my God this traffic" filed as a prayer - is a phantom on
the Faith screen, and worse, it removes the todo it was read from. Both are visible and
one tap to fix, so the bars are close; precision is a little higher because a phantom
also costs a todo.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from memo.schemas.faith import FaithExtraction
from memo.services.people import normalize

THRESHOLDS: dict[str, float] = {
	"prayer_recall": 0.85,
	"prayer_precision": 0.90,
	"person_accuracy": 0.85,
}


class Expected(BaseModel):
	# A word the prayer's title (or who it's for) should contain: titles are free text,
	# so the match is loose on purpose.
	match: str
	# Who it's for, as the note names them; checked only when given.
	for_: str | None = Field(default=None, alias="for")


@dataclass
class Counts:
	tp: int = 0
	fp: int = 0
	fn: int = 0
	people_right: int = 0
	people_total: int = 0

	def add(self, other: "Counts") -> None:
		for name in self.__dataclass_fields__:
			setattr(self, name, getattr(self, name) + getattr(other, name))

	@staticmethod
	def _ratio(hit: int, miss: int) -> float:
		return hit / (hit + miss) if hit + miss else 1.0

	def metrics(self) -> dict[str, float]:
		return {
			"prayer_recall": self._ratio(self.tp, self.fn),
			"prayer_precision": self._ratio(self.tp, self.fp),
			"person_accuracy": self._ratio(
				self.people_right, self.people_total - self.people_right
			),
		}


def score_example(expected: list[Expected], predicted: FaithExtraction) -> Counts:
	counts = Counts()
	remaining = list(expected)
	for prayer in predicted.prayers:
		said = f"{prayer.title} {prayer.for_person or ''}".casefold()
		hit = next((e for e in remaining if e.match.casefold() in said), None)
		if hit is None:
			counts.fp += 1
			continue
		remaining.remove(hit)
		counts.tp += 1
		if hit.for_ is not None:
			counts.people_total += 1
			counts.people_right += int(
				prayer.for_person is not None
				and normalize(prayer.for_person) == normalize(hit.for_)
			)
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
			"wrong person": counts.people_total - counts.people_right,
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
			f"{name:<17} {value:>5.2f}  (bar {THRESHOLDS[name]:.2f})"
			for name, value in self.totals.metrics().items()
		)
