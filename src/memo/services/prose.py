"""Text memo writes, made to follow the house rule the prompt only asks for (ADR 0061):
no em dashes. A model told not to use them mostly doesn't; this is the "mostly".

Only for text a model wrote. A note, a caption, a quote from a note and anything the
person typed keep every dash they have: iOS turns "--" into an em dash as you type, and
that one is theirs. En dashes are left alone ("John 3–4").
"""

import re

EM_DASH = "—"

# A dash between words, spaced or not: "the launch — again" and "the launch—again" are
# both "the launch, again".
_BETWEEN = re.compile(r"\s*—\s*")
# What a replaced dash can leave next to other punctuation: ", ." or ",,".
_DOUBLED = re.compile(r",\s*([,.;:!?)\]])")
_OPENED = re.compile(r"([(\[])\s*,\s*")
# What a stream holds back until the next piece says what follows it.
_TAIL = " \t\r\n" + EM_DASH


def _replace(text: str) -> str:
	out = _BETWEEN.sub(", ", text)
	out = _DOUBLED.sub(r"\1", out)
	return _OPENED.sub(r"\1", out)


def plain_dashes(text: str) -> str:
	"""The same text with each em dash made a comma, tidied where that would double
	up punctuation. A dash that opens or closes the text is dropped."""
	if EM_DASH not in text:
		return text
	out = _replace(text)
	out = out.removeprefix(", ")
	return out.removesuffix(", ")


class DashFilter:
	"""plain_dashes for text that arrives in pieces (Ask's streamed answer), so what the
	person watches appear is exactly what is saved.

	Whitespace and dashes at the end of a piece are held until the next piece shows
	what follows them: "the launch " then "— again" comes out "the launch, again", and
	"— ." comes out "." rather than ", .". The cost is one token of delay on a space."""

	def __init__(self) -> None:
		self._held = ""
		self._started = False

	def feed(self, piece: str) -> str:
		text = self._held + piece
		cut = len(text.rstrip(_TAIL))
		ready, self._held = text[:cut], text[cut:]
		if not ready:
			return ""
		out = _replace(ready)
		if not self._started:
			out = out.removeprefix(", ")  # the answer opened with a dash
			self._started = True
		return out

	def close(self) -> str:
		"""What is still held when the stream ends. A dash left hanging is dropped."""
		held, self._held = self._held, ""
		return held.replace(EM_DASH, "").rstrip() if EM_DASH in held else held
