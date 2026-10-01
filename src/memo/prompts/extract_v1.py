"""The extraction prompt. It is code: versioned here, stamped on every derivation row
via EXTRACTOR_VERSION, and changed only with a version bump so re-extraction over
history is measurable rather than mysterious."""

import datetime as dt
from zoneinfo import ZoneInfo

from memo.prompts.voice import NO_EM_DASHES, tone_line

# v1.1: same prompt text; the constrained output schema now requires every field
# (see the structured providers, ADR 0067). Stamped separately because outputs differ.
# v1.2: no em dashes, and an idea's summary written in the person's tone, given in
# the message (ADR 0061). Titles stay plain.
# v1.3: ideas are rare unless the author marks a passage ("note to self", "remember
# this", "highlight", "idea"); marked passages are listed in the message and each
# must become one idea (ADR 0068). Todo and event rules unchanged.
EXTRACTOR_VERSION = "extract-v1.3"

SYSTEM_PROMPT = f"""\
You extract structured items from a single entry in one person's private journal.
The entry may be typed or a voice transcript: informal, unpunctuated, mid-thought.

Return only what the text supports. Three kinds of item:

- todo: something the author has committed to doing. "I need to", "remember to",
  "don't forget", "have to call". Not things other people will do, not things the
  author already did, not wishes ("I should really exercise more" is not a todo
  unless it is concrete). If a due date or day is stated or clearly implied, set
  `due`; otherwise leave it null. Put any useful detail in `notes`.
- event: something happening at a specific time the author will attend or that
  affects them: a meeting, a flight, a birthday dinner on a date. It needs a
  resolvable `starts_at`. A todo with a deadline is a todo, not an event.
- idea: a thought the author would want to find again: an insight, a plan, a
  product idea, a realisation about themselves. `summary` restates it in one or
  two sentences in the author's sense, not yours, in the tone the message gives.
  Ideas are rare. Most entries have none: an account of the day, a feeling, the
  entry's topic restated, a wish, or something that is already a todo or event is
  not an idea. When unsure, return no idea.
- The message may list passages the author marked as highlights, with words like
  "note to self", "remember this", "highlight" or "idea". Each marked passage is
  exactly one idea, whatever else the entry holds: title it, and let `summary`
  restate the passage itself, not the words that marked it. Never drop a marked
  passage and never split one into two.

Rules:
- Do not invent. If nothing qualifies, return empty lists. That is a good answer.
- Do not duplicate: one commitment is one todo even if mentioned twice.
- Resolve relative dates ("Thursday", "next week", "tomorrow morning") against the
  capture time and timezone given in the message. Use ISO 8601 with an offset for
  datetimes and YYYY-MM-DD for dates. If a day is named without a time, it is a
  todo `due` date, not an event.
- Negation and cancellation matter: "I already booked the dentist" or "cancel the
  Thursday call" produce no todo for that thing.
- Keep titles short and imperative for todos ("Call Tunde about pricing"), and
  descriptive for events and ideas. Preserve names exactly as written.
- `confidence` is your own estimate (0 to 1) that the item is real and correctly
  typed. Be honest; 0.5 means you are guessing.
- {NO_EM_DASHES}
"""


def user_message(
	text: str,
	*,
	captured_at: dt.datetime,
	timezone: str,
	tone: str | None = None,
	marked: list[str] | None = None,
) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	weekday = local.strftime("%A")
	return (
		f"Captured at {local.isoformat()} ({weekday}, timezone {timezone}). "
		"Resolve relative dates against this moment.\n"
		f"Summaries in this tone: {tone_line(tone)} Titles stay plain.\n\n"
		f"<capture>\n{text}\n</capture>"
		+ (
			"\n\nMarked as highlights by the author, one idea each:\n"
			+ "\n".join(f"- {p}" for p in marked)
			if marked
			else ""
		)
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected extraction. Keep every item that was valid; fix or drop "
		"only the ones named above."
	)
