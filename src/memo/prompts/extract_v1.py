"""The extraction prompt. It is code: versioned here, stamped on every derivation row
via EXTRACTOR_VERSION, and changed only with a version bump so re-extraction over
history is measurable rather than mysterious."""

import datetime as dt
from zoneinfo import ZoneInfo

EXTRACTOR_VERSION = "extract-v1"

SYSTEM_PROMPT = """\
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
  two sentences in the author's sense, not yours.

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
"""


def user_message(text: str, *, captured_at: dt.datetime, timezone: str) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	weekday = local.strftime("%A")
	return (
		f"Captured at {local.isoformat()} ({weekday}, timezone {timezone}). "
		"Resolve relative dates against this moment.\n\n"
		f"<capture>\n{text}\n</capture>"
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected extraction. Keep every item that was valid; fix or drop "
		"only the ones named above."
	)
