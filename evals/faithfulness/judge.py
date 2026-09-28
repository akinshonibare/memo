"""Does the answer follow from the cited chunks? (issue #37)

Two judges with one verdict shape. The heuristic one is deterministic and offline:
a claim is supported when enough of its content words appear in some cited chunk.
The LLM judge reads the claim and the chunks together and is the real thing; it needs
a key, so the heuristic is what CI runs and what the calibration set validates.
"""

import re
from dataclasses import dataclass, field
from typing import Protocol

from anthropic import AsyncAnthropic
from anthropic.lib._parse._transform import transform_schema
from pydantic import BaseModel

_WORD = re.compile(r"[a-z0-9']+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
_STOP = {
	"the", "a", "an", "to", "and", "of", "for", "on", "in", "at", "with", "about",
	"you", "your", "i", "my", "me", "is", "are", "was", "were", "be", "it", "that",
	"this", "notes", "note", "wrote", "say", "says", "said", "mention", "mentioned",
	"have", "has", "had", "do", "did", "not", "no", "so", "as", "by", "from", "or",
}  # fmt: skip
_HEDGES = ("don't mention", "do not mention", "no note", "nothing in your notes")


@dataclass
class Verdict:
	faithful: bool
	unsupported: list[str] = field(default_factory=list)


class Judge(Protocol):
	async def judge(self, *, answer: str, cited: list[str]) -> Verdict: ...


def _stem(word: str) -> str:
	"""Crude but symmetric: 'needed' and 'need', 'owes' and 'owe' should match."""
	if word.endswith("ies") and len(word) > 4:
		return word[:-3] + "y"
	if word.endswith("ing") and len(word) > 5:
		return word[:-3]
	if word.endswith("ed") and len(word) > 4:
		return word[:-2]
	if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
		return word[:-1]
	return word


def _content(text: str) -> set[str]:
	return {
		_stem(w) for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2
	}


def _claims(answer: str) -> list[str]:
	cleaned = re.sub(r"\[\d+\]", "", answer)
	return [
		s.strip()
		for s in _SENTENCE.split(cleaned)
		if s.strip() and not any(h in s.lower() for h in _HEDGES)
	]


class HeuristicJudge:
	def __init__(self, threshold: float = 0.7) -> None:
		self.threshold = threshold

	async def judge(self, *, answer: str, cited: list[str]) -> Verdict:
		pool = [_content(c) for c in cited]
		unsupported = []
		for claim in _claims(answer):
			words = _content(claim)
			if not words:
				continue
			best = max((len(words & c) / len(words) for c in pool), default=0.0)
			if best < self.threshold:
				unsupported.append(claim)
		return Verdict(faithful=not unsupported, unsupported=unsupported)


class _LLMVerdict(BaseModel):
	faithful: bool
	unsupported_claims: list[str]


_JUDGE_SYSTEM = """\
You check whether an assistant's answer is supported by the notes it was given. A claim
is supported if a note states it or it follows directly from one. Restating that the
notes do not cover something is not a claim. List every unsupported claim verbatim;
faithful is true only when there are none.
"""


class LLMJudge:
	def __init__(self, client: AsyncAnthropic, *, model: str = "claude-opus-5") -> None:
		self._client = client
		self._model = model
		self._schema = transform_schema(_LLMVerdict)

	async def judge(self, *, answer: str, cited: list[str]) -> Verdict:
		notes = "\n\n".join(f"[{i}] {c}" for i, c in enumerate(cited, start=1))
		response = await self._client.messages.create(
			model=self._model,
			max_tokens=1024,
			system=_JUDGE_SYSTEM,
			messages=[
				{
					"role": "user",
					"content": (
						f"<notes>\n{notes}\n</notes>\n\n<answer>\n{answer}\n</answer>"
					),
				}
			],
			output_config={"format": {"type": "json_schema", "schema": self._schema}},
		)
		text = next(b.text for b in response.content if b.type == "text")
		parsed = _LLMVerdict.model_validate_json(text)
		return Verdict(faithful=parsed.faithful, unsupported=parsed.unsupported_claims)
