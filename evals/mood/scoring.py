"""Scoring the mood reader (ADR 0058): does it hear how the author sounds, only when the
note shows it, and never the wrong way round?

The errors are not equal. A phantom mood on a to-do note puts a dot on a day that said
nothing, so precision's bar is higher than recall's: a missed mood is only a quiet day.
A read one step off (heavy for low) is forgivable; the wrong way round (a grieving note
read as good) is the error that would make someone stop trusting the screen, so it has
its own gate at 1.00. Every read must quote the note: evidence is what the day sheet
shows, and a paraphrase puts words in the author's mouth.
"""

from dataclasses import dataclass, field

from pydantic import BaseModel

from memo.schemas.mood import MoodRead
from memo.services.mood import quoted

THRESHOLDS: dict[str, float] = {
	"mood_recall": 0.85,
	"mood_precision": 0.90,
	"valence_accuracy": 0.80,
	"within_one": 0.95,
	"no_sign_flip": 1.00,
	"evidence_quoted": 0.90,
}


class Expected(BaseModel):
	# The best answer; null when the note shows no mood.
	valence: int | None
	# Every answer that is also right: a quiet day read as steady or as nothing, a
	# thankful note as good or bright. Defaults to [valence].
	accept: list[int | None] | None = None

	def acceptable(self) -> list[int | None]:
		return self.accept if self.accept is not None else [self.valence]


@dataclass
class Counts:
	has_mood: int = 0  # cases where a mood must be heard (null not acceptable)
	heard: int = 0  # of those, how many were read at all
	read: int = 0  # reads made
	read_rightly: int = 0  # reads on a case where some mood is acceptable
	judged: int = 0  # reads on a case with a best valence
	accurate: int = 0  # read valence among the acceptable ones
	near: int = 0  # within one step of the best valence
	flipped: int = 0  # the wrong side of steady
	quoted: int = 0  # evidence found in the note

	def add(self, other: "Counts") -> None:
		for name in self.__dataclass_fields__:
			setattr(self, name, getattr(self, name) + getattr(other, name))

	@staticmethod
	def _ratio(hit: int, total: int) -> float:
		return hit / total if total else 1.0

	def metrics(self) -> dict[str, float]:
		return {
			"mood_recall": self._ratio(self.heard, self.has_mood),
			"mood_precision": self._ratio(self.read_rightly, self.read),
			"valence_accuracy": self._ratio(self.accurate, self.judged),
			"within_one": self._ratio(self.near, self.judged),
			"no_sign_flip": 1.0 - self._ratio(self.flipped, self.judged)
			if self.judged
			else 1.0,
			"evidence_quoted": self._ratio(self.quoted, self.read),
		}


def score_example(text: str, expected: Expected, predicted: MoodRead) -> Counts:
	counts = Counts()
	acceptable = expected.acceptable()
	if None not in acceptable:
		counts.has_mood = 1
		counts.heard = int(predicted.valence is not None)
	if predicted.valence is None:
		return counts
	counts.read = 1
	counts.read_rightly = int(any(v is not None for v in acceptable))
	counts.quoted = int(quoted(predicted.evidence, text) is not None)
	if expected.valence is not None:
		counts.judged = 1
		counts.accurate = int(predicted.valence in acceptable)
		counts.near = int(abs(predicted.valence - expected.valence) <= 1)
		counts.flipped = int(predicted.valence * expected.valence < 0)
	return counts


@dataclass
class Report:
	totals: Counts = field(default_factory=Counts)
	examples: int = 0
	misses: list[str] = field(default_factory=list)

	def add(self, example_id: str, counts: Counts, said: str) -> None:
		self.examples += 1
		self.totals.add(counts)
		wrong = {
			"missed the mood": counts.has_mood - counts.heard,
			"a mood the note doesn't show": counts.read - counts.read_rightly,
			"wrong step": counts.judged - counts.accurate,
			"the wrong way round": counts.flipped,
			"evidence not in the note": counts.read - counts.quoted,
		}
		for what, n in wrong.items():
			if n:
				self.misses.append(f"{example_id}: {what} ({said})")

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
