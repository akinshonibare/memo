"""Per-kind precision and recall for extraction, matched on title similarity.

Recall and precision are not symmetric here (NFR4): a missed todo erodes trust
quietly and permanently; an invented one is visible and one tap to dismiss. So the
todo recall bar is higher than its precision bar.
"""

import re
from dataclasses import dataclass, field

from memo.schemas.derivations import KINDS, Extraction

THRESHOLDS: dict[str, dict[str, float]] = {
	"todo": {"recall": 0.90, "precision": 0.80},
	"event": {"recall": 0.85, "precision": 0.80},
	"idea": {"recall": 0.70, "precision": 0.70},
}

_WORD = re.compile(r"[a-z0-9']+")
_STOP = {"the", "a", "an", "to", "and", "of", "for", "on", "in", "at", "with", "about"}


def _stem(word: str) -> str:
	"""Crude suffix stripping, so "scores"/"score" and "enqueuing"/"enqueue" count
	as one word. Deliberately not a real stemmer: it only has to beat exact match on
	short titles, and every rule here was checked against the whole golden set for
	new false matches (none)."""
	for suffix in ("ing", "ed", "es", "s", "e"):
		if len(word) > len(suffix) + 3 and word.endswith(suffix):
			return word[: -len(suffix)]
	return word


def _tokens(text: str) -> set[str]:
	return {_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP}


def titles_match(a: str, b: str) -> bool:
	"""Loose enough to survive rephrasing, strict enough to keep 'call Tunde' and
	'call the dentist' apart."""
	ta, tb = _tokens(a), _tokens(b)
	if not ta or not tb:
		return a.strip().lower() == b.strip().lower()
	overlap = len(ta & tb) / len(ta | tb)
	return overlap >= 0.5 or ta <= tb or tb <= ta


@dataclass
class Counts:
	tp: int = 0
	fp: int = 0
	fn: int = 0

	@property
	def precision(self) -> float:
		return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 1.0

	@property
	def recall(self) -> float:
		return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 1.0

	def add(self, other: "Counts") -> None:
		self.tp += other.tp
		self.fp += other.fp
		self.fn += other.fn


def score_example(expected: Extraction, predicted: Extraction) -> dict[str, Counts]:
	by_kind: dict[str, Counts] = {kind: Counts() for kind in KINDS}
	exp: dict[str, list[str]] = {k: [] for k in KINDS}
	pred: dict[str, list[str]] = {k: [] for k in KINDS}
	for kind, item in expected.items():
		exp[kind].append(item.title)
	for kind, item in predicted.items():
		pred[kind].append(item.title)

	for kind in KINDS:
		remaining = list(exp[kind])
		for title in pred[kind]:
			hit = next((e for e in remaining if titles_match(title, e)), None)
			if hit is None:
				by_kind[kind].fp += 1
			else:
				remaining.remove(hit)
				by_kind[kind].tp += 1
		by_kind[kind].fn += len(remaining)
	return by_kind


@dataclass
class Report:
	by_kind: dict[str, Counts] = field(
		default_factory=lambda: {kind: Counts() for kind in KINDS}
	)
	examples: int = 0
	misses: list[str] = field(default_factory=list)

	def add(self, example_id: str, scores: dict[str, Counts]) -> None:
		self.examples += 1
		for kind, counts in scores.items():
			self.by_kind[kind].add(counts)
			if counts.fn or counts.fp:
				self.misses.append(
					f"{example_id}: {kind} fn={counts.fn} fp={counts.fp}"
				)

	def failures(self) -> list[str]:
		out = []
		for kind, bars in THRESHOLDS.items():
			counts = self.by_kind[kind]
			if counts.recall < bars["recall"]:
				out.append(f"{kind} recall {counts.recall:.2f} < {bars['recall']:.2f}")
			if counts.precision < bars["precision"]:
				out.append(
					f"{kind} precision {counts.precision:.2f} < {bars['precision']:.2f}"
				)
		return out

	def table(self) -> str:
		lines = [f"{'kind':<6} {'tp':>4} {'fp':>4} {'fn':>4} {'prec':>6} {'rec':>6}"]
		for kind in KINDS:
			c = self.by_kind[kind]
			lines.append(
				f"{kind:<6} {c.tp:>4} {c.fp:>4} {c.fn:>4} "
				f"{c.precision:>6.2f} {c.recall:>6.2f}"
			)
		return "\n".join(lines)
