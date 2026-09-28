"""Length-dependent chunking (ADR 0017).

Short captures are one chunk: a 40-word note split in two is two half-thoughts and a
citation that points at half a sentence. Longer text splits on structure - paragraphs,
then sentences - packed to a token target, with a little overlap so a boundary never
cuts a sentence in half. Every chunk is a contiguous span of the source, so
`source[char_start:char_end] == chunk.text` always holds and a citation can highlight.
"""

import re
from dataclasses import dataclass

CHUNKER_VERSION = "chunk-v1"

SHORT_TOKENS = 200
TARGET_TOKENS = 400
OVERLAP_TOKENS = 60
MIN_TAIL_TOKENS = 50

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class TextChunk:
	ordinal: int
	text: str
	char_start: int
	char_end: int


def estimate_tokens(text: str) -> int:
	"""chars/4: within ~20% for English prose, and no tokenizer dependency."""
	return max(1, len(text) // 4)


def _spans(text: str, pattern: re.Pattern[str], offset: int) -> list[tuple[int, int]]:
	"""Non-empty spans between matches of `pattern`, as absolute offsets."""
	spans = []
	start = 0
	for match in pattern.finditer(text):
		if text[start : match.start()].strip():
			spans.append((offset + start, offset + match.start()))
		start = match.end()
	if text[start:].strip():
		spans.append((offset + start, offset + len(text)))
	return spans


def _units(source: str) -> list[tuple[int, int]]:
	"""Paragraphs; paragraphs over the target become sentences; sentences over the
	target become fixed windows. Returned in source order."""
	units: list[tuple[int, int]] = []
	for p_start, p_end in _spans(source, _PARAGRAPH_BREAK, 0):
		paragraph = source[p_start:p_end]
		if estimate_tokens(paragraph) <= TARGET_TOKENS:
			units.append((p_start, p_end))
			continue
		for s_start, s_end in _spans(paragraph, _SENTENCE_END, p_start):
			sentence = source[s_start:s_end]
			if estimate_tokens(sentence) <= TARGET_TOKENS:
				units.append((s_start, s_end))
				continue
			window = TARGET_TOKENS * 4
			for w in range(s_start, s_end, window):
				units.append((w, min(w + window, s_end)))
	return units


def _trim(source: str, start: int, end: int) -> tuple[int, int]:
	while start < end and source[start].isspace():
		start += 1
	while end > start and source[end - 1].isspace():
		end -= 1
	return start, end


def chunk_text(source: str) -> list[TextChunk]:
	start, end = _trim(source, 0, len(source))
	if start >= end:
		return []
	if estimate_tokens(source[start:end]) <= SHORT_TOKENS:
		return [TextChunk(0, source[start:end], start, end)]

	units = _units(source)
	groups: list[tuple[int, int]] = []  # (core_start, core_end)
	g_start, g_end = units[0]
	for u_start, u_end in units[1:]:
		if estimate_tokens(source[g_start:u_end]) > TARGET_TOKENS:
			groups.append((g_start, g_end))
			g_start, g_end = u_start, u_end
		else:
			g_end = u_end
	groups.append((g_start, g_end))

	# A tiny tail is merged into its predecessor rather than emitted as a fragment.
	if len(groups) > 1:
		t_start, t_end = groups[-1]
		if estimate_tokens(source[t_start:t_end]) < MIN_TAIL_TOKENS:
			p_start, _ = groups.pop()
			groups[-1] = (groups[-1][0], t_end)

	chunks: list[TextChunk] = []
	overlap_chars = OVERLAP_TOKENS * 4
	for ordinal, (core_start, core_end) in enumerate(groups):
		span_start = core_start
		if ordinal > 0:
			# Overlap: reach back into the previous core, snapping to a word boundary.
			candidate = max(groups[ordinal - 1][0], core_start - overlap_chars)
			boundary = source.rfind(" ", candidate, core_start)
			span_start = candidate if boundary == -1 else boundary + 1
		span_start, span_end = _trim(source, span_start, core_end)
		chunks.append(
			TextChunk(ordinal, source[span_start:span_end], span_start, span_end)
		)
	return chunks
