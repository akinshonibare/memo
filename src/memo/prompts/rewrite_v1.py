"""Query rewriting for multi-turn ask (issue #35): "what about the other one?" carries
no retrievable signal on its own, so it is resolved against recent history into a
standalone query before retrieval. Skipped on the first turn, and when the question
looks self-contained, because it sits on the time-to-first-token budget."""

import re

REWRITE_VERSION = "rewrite-v1"

SYSTEM_PROMPT = """\
You rewrite the user's latest question into a single standalone search query that
carries everything needed to find the answer in their journal, using the conversation
so far to resolve pronouns and references ("it", "that", "the other one", "him").
Output only the rewritten query, no preamble. If the question is already standalone,
output it unchanged.
"""

# Deictic and anaphoric markers: if none appear, the question can stand on its own and
# the rewrite call is skipped (the cheapest mitigation in the design doc's list).
_NEEDS_CONTEXT = re.compile(
	r"\b(it|its|that|this|those|these|the other|the same|he|she|him|her|his|hers|"
	r"they|them|their|again|earlier|before|one)\b",
	re.IGNORECASE,
)


def needs_rewrite(question: str, prior_turns: int) -> bool:
	return prior_turns > 0 and bool(_NEEDS_CONTEXT.search(question))


def user_turn(history: list[tuple[str, str]], question: str) -> str:
	lines = [f"{role}: {text}" for role, text in history]
	return (
		"<conversation>\n"
		+ "\n".join(lines)
		+ "\n</conversation>\n\n"
		+ f"Latest question: {question}\n\nStandalone query:"
	)
