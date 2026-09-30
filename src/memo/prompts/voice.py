"""How memo writes (ADR 0061): one tone, chosen per person, for the prose memo writes
for them (Ask's answers, highlight summaries, photo descriptions), and one rule for
everyone: no em dashes.

Code like every prompt: a change here changes what the answer, extraction and describe
prompts say, so those bump their versions with it. Titles, labels and anything quoted
from a note are never toned: you scan titles, and quotes are the author's own words.
"""

from typing import Literal

Tone = Literal["plain", "warm", "brief", "playful"]
TONES: tuple[Tone, ...] = ("plain", "warm", "brief", "playful")
DEFAULT_TONE: Tone = "plain"

# The em dash is the tell of machine-written prose (Akin, 2026-09-30), and memo writes
# for one person, not a magazine. The prompt asks; services/prose makes sure.
NO_EM_DASHES = (
	"Never use em dashes. Where one might go, use a comma, a colon, parentheses or a "
	"new sentence."
)

_TONE = {
	"plain": "Plain: clear, neutral and direct. No flourishes.",
	"warm": (
		"Warm: friendly and a little personal, encouraging where it fits. Never "
		"gushing, never sugary."
	),
	"brief": (
		"Brief: as few words as the facts allow. Short sentences, no filler, no "
		"pleasantries."
	),
	"playful": (
		"Playful: a light touch and a little wit are welcome. Never joke about grief, "
		"illness, money trouble, faith or anything the note treats as serious; there, "
		"write warmly and plainly instead."
	),
}


def as_tone(value: str | None) -> Tone:
	"""A stored tone, or the default for anything unknown (an old row, a typo)."""
	return value if value in TONES else DEFAULT_TONE


def tone_line(tone: str | None) -> str:
	return _TONE[as_tone(tone)]


def writing_rules(tone: str | None) -> str:
	"""The block appended to a prompt that writes prose for this person."""
	return (
		"How to write for this person:\n"
		f"- Tone: {tone_line(tone)} The tone changes how things are said, never what "
		"is true or what is left out.\n"
		f"- {NO_EM_DASHES}"
	)
