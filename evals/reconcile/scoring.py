"""Scoring the reconciler (ADR 0048): does it find the earlier items a note changes,
and only those?

A wrong "answered" is the expensive error: the author is asked about a prayer that
wasn't answered, and a careless yes marks it so. A missed one costs a tap in Faith. So
precision has the higher bar.
"""

from dataclasses import dataclass

from memo.schemas.reconcile import Reconciliation

THRESHOLDS: dict[str, float] = {
	"precision": 0.90,
	"recall": 0.75,
}

Pair = tuple[str, str]  # (label, relation)


@dataclass
class Counts:
	tp: int = 0
	fp: int = 0
	fn: int = 0

	def add(self, other: "Counts") -> None:
		self.tp += other.tp
		self.fp += other.fp
		self.fn += other.fn

	def metrics(self) -> dict[str, float]:
		def ratio(hit: int, miss: int) -> float:
			return hit / (hit + miss) if hit + miss else 1.0

		return {
			"precision": ratio(self.tp, self.fp),
			"recall": ratio(self.tp, self.fn),
		}


def score_example(expected: list[Pair], predicted: Reconciliation) -> Counts:
	want = set(expected)
	got = {(c.candidate, c.relation) for c in predicted.changes}
	return Counts(tp=len(want & got), fp=len(got - want), fn=len(want - got))


@dataclass
class Report:
	counts: Counts
	misses: list[str]
	examples: int = 0

	@classmethod
	def empty(cls) -> "Report":
		return cls(Counts(), [])

	def add(self, example_id: str, counts: Counts) -> None:
		self.examples += 1
		self.counts.add(counts)
		if counts.fp or counts.fn:
			self.misses.append(f"{example_id}: fp={counts.fp} fn={counts.fn}")

	def table(self) -> str:
		return "\n".join(
			f"{name:<10} {value:.2f}  (bar {THRESHOLDS[name]:.2f})"
			for name, value in self.counts.metrics().items()
		)

	def failures(self) -> list[str]:
		return [
			f"{name} {value:.2f} < {THRESHOLDS[name]:.2f}"
			for name, value in self.counts.metrics().items()
			if value < THRESHOLDS[name]
		]
