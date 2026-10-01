"""Explicit highlights (ADR 0068): the words a person uses to say "keep this".

A highlight the reader guesses waits for a look. One the author marks does not: say
"note to self", "remember this", "highlight" or "idea" and that passage becomes an
idea whatever the model makes of the rest. The marking is deterministic and lives
here, in code, so it is the same on every model and testable without one; the
model's job is to word the title and summary, and if it fails to, the passage itself
stands in.
"""

import re

from memo.schemas.derivations import Extraction, IdeaPayload

# Word-bounded, case-insensitive. Each alternative is a way people say "keep this".
# The "idea" family is guarded below against "no idea", "good idea" and the like.
_TRIGGER = re.compile(
	r"(?<![\w#])(?:"
	r"note to self|remember this|want to remember|worth remembering|write this down"
	r"|mark this|highlight(?: this)?|key takeaway|takeaway|insight"
	r"|(?:(?:i have|i've got) an|an|a new|new|big|random|product|app|business) idea"
	r"(?: for (?:the |an? |my )?\w+)?"
	r"|idea(?: for (?:the |an? |my )?\w+)?(?= ?[:,])|the idea is"
	r"|#highlight|#idea"
	r")\b[:,.\-]?",
	re.IGNORECASE,
)
# "good idea" is approval, "no idea" is ignorance: neither marks anything.
_NOT_AN_IDEA = re.compile(
	r"\b(?:no|any|good|bad|great|nice|some|every|general|rough|the slightest|whose|"
	r"what|your|his|her|their) idea\b",
	re.IGNORECASE,
)
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|\n+")
# Filler a passage often starts with once the trigger is cut out of it.
_LEADING = re.compile(
	r"^(?:(?:ok|okay|so|yeah|yes|oh|and|but|that|basically|like|um|erm|right|well|also"
	r"|anyway)[,\s]+)+",
	re.IGNORECASE,
)
_WORD = re.compile(r"[a-z0-9']+")
# Longer than this and a mark names a sentence, not the whole note.
SHORT_NOTE = 240
MAX_PASSAGE = 300
MAX_MARKS = 5
MIN_WORDS = 3


def marked_passages(text: str) -> list[str]:
	"""The passages the author marked as highlights, trigger words removed, in order
	of appearance. Empty when nothing is marked, which is almost always."""
	if not text or not text.strip():
		return []
	sentences = [s.strip() for s in _SENTENCE_END.split(text) if s.strip()]
	found: list[str] = []
	for index, sentence in enumerate(sentences):
		hits = [m for m in _TRIGGER.finditer(sentence) if _is_mark(sentence, m)]
		if not hits:
			continue
		# Only the first trigger is removed: a later "highlight" or "idea" in the
		# same sentence is usually the noun ("read the top highlight out loud").
		passage = _without(sentence, hits[:1])
		if len(_WORD.findall(passage)) < MIN_WORDS and index + 1 < len(sentences):
			# "Note to self." on its own: the next sentence is the thought.
			passage = _without(sentences[index + 1], [])
		if len(_WORD.findall(passage)) < MIN_WORDS:
			continue
		# A tag on a short note marks the note; on a long one, its sentence.
		if any(m.group(0).startswith("#") for m in hits) and len(text) <= SHORT_NOTE:
			passage = _without(text, list(_TRIGGER.finditer(text)))
		passage = _clip(passage)
		if passage.lower() not in (p.lower() for p in found):
			found.append(passage)
		if len(found) == MAX_MARKS:
			break
	return found


def _is_mark(sentence: str, match: re.Match[str]) -> bool:
	if "idea" not in match.group(0).lower():
		return True
	# The two words before "idea" decide: "an idea" marks, "no idea" doesn't.
	before = sentence[max(0, match.start() - 24) : match.end()]
	return _NOT_AN_IDEA.search(before) is None


def _without(sentence: str, hits: list[re.Match[str]]) -> str:
	spans = sorted((m.start(), m.end()) for m in hits)
	out: list[str] = []
	last = 0
	for start, end in spans:
		out.append(sentence[last:start])
		last = end
	out.append(sentence[last:])
	passage = re.sub(r"\s+", " ", "".join(out)).strip(" ,;:-.!?")
	passage = _LEADING.sub("", passage).strip(" ,;:-.!?")
	return passage[:1].upper() + passage[1:] if passage else passage


def _clip(passage: str) -> str:
	if len(passage) <= MAX_PASSAGE:
		return passage
	cut = passage[:MAX_PASSAGE].rsplit(" ", 1)[0]
	return cut.rstrip(" ,;:-")


def title_for(passage: str, *, limit: int = 80) -> str:
	"""A title when the model gave the marked passage none: its first words."""
	first = _SENTENCE_END.split(passage)[0].strip().rstrip(".!?,;:")
	if len(first) <= limit:
		return first
	return first[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")


def overlap(mark: str, text: str) -> float:
	"""How much of the mark's words the text contains, 0 to 1. Used to pair the
	author's marks with the ideas the model wrote, not to judge them."""
	a = set(_WORD.findall(mark.lower()))
	b = set(_WORD.findall(text.lower()))
	return len(a & b) / len(a) if a else 0.0


# Below this much word overlap a model idea is not "the one for this mark" unless it
# is the only idea left for it.
PAIR_OVERLAP = 0.3


def ensure_marked(
	extraction: Extraction, marks: list[str]
) -> tuple[Extraction, frozenset[int]]:
	"""Every mark ends as exactly one idea. Each mark takes the model's idea that
	best restates it (one idea per mark, so nothing is duplicated); a mark the model
	gave no idea becomes one itself, the passage as its summary. Returns the
	extraction and the indexes of the ideas that came from marks."""
	if not marks:
		return extraction, frozenset()
	ideas = list(extraction.ideas)
	taken: set[int] = set()
	marked: set[int] = set()
	for position, mark in enumerate(marks):
		remaining = len(marks) - position
		candidates = [
			(overlap(mark, f"{idea.title} {idea.summary}"), index)
			for index, idea in enumerate(ideas)
			if index not in taken
		]
		if candidates and (
			max(candidates)[0] >= PAIR_OVERLAP or len(candidates) >= remaining
		):
			index = max(candidates)[1]
		else:
			ideas.append(
				IdeaPayload(title=title_for(mark), summary=mark, confidence=1.0)
			)
			index = len(ideas) - 1
		taken.add(index)
		marked.add(index)
	return extraction.model_copy(update={"ideas": ideas}), frozenset(marked)
