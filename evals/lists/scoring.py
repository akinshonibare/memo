"""Scoring the lists reader (ADR 0069): does it put on a list what the author meant for
one, on the right list, and nothing else?

A missed item never reaches the list; the author adds it by hand or forgets the milk.
An invented one is a phantom on the list; one tap to remove, but it is the error people
see and lose trust over, so precision has the higher bar. The list is checked by its
key, so "the shopping list" and "groceries" agree.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

from memo.schemas.lists import ListsExtraction
from memo.services.lists import list_key
from memo.services.people import normalize

THRESHOLDS: dict[str, float] = {
	"item_recall": 0.85,
	"item_precision": 0.90,
	"list_accuracy": 0.90,
}


class Expected(BaseModel):
	# A word the item's title should contain; titles are free text, so loose on purpose.
	match: str
	# The list it belongs on, compared by key ("shopping list" is "groceries").
	list: str


@dataclass
class Counts:
	tp: int = 0
	fp: int = 0
	fn: int = 0
	list_right: int = 0

	def add(self, other: "Counts") -> None:
		for name in self.__dataclass_fields__:
			setattr(self, name, getattr(self, name) + getattr(other, name))

	@staticmethod
	def _ratio(hit: int, miss: int) -> float:
		return hit / (hit + miss) if hit + miss else 1.0

	def metrics(self) -> dict[str, float]:
		return {
			"item_recall": self._ratio(self.tp, self.fn),
			"item_precision": self._ratio(self.tp, self.fp),
			"list_accuracy": self._ratio(self.list_right, self.tp - self.list_right),
		}


def score_example(expected: list[Expected], predicted: ListsExtraction) -> Counts:
	counts = Counts()
	remaining = list(expected)
	for item in predicted.items:
		said = normalize(item.title)
		hit = next((e for e in remaining if normalize(e.match) in said), None)
		if hit is None:
			counts.fp += 1
			continue
		remaining.remove(hit)
		counts.tp += 1
		counts.list_right += int(list_key(item.list) == list_key(hit.list))
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
			"wrong list": counts.tp - counts.list_right,
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
			f"{name:<15} {value:>5.2f}  (bar {THRESHOLDS[name]:.2f})"
			for name, value in self.totals.metrics().items()
		)
