"""The reconcile prompt (ADR 0048): which earlier items a new note changes. Code like
every prompt: versioned here, stamped on every suggestion it produces, changed only
with a bump.

Its own prompt, run after a note is saved and searchable, only when there is something
to compare it with: open prayers (for someone with Faith on, and an answer-ish word in
the note) or ideas close to the note in meaning. Most notes never reach it.
"""

import datetime as dt
from dataclasses import dataclass
from zoneinfo import ZoneInfo

RECONCILE_VERSION = "reconcile-v1"

SYSTEM_PROMPT = """\
You read one new entry in a person's private journal, next to a short numbered list of
things they wrote down earlier: open prayers (P1, P2, ...) and ideas (I1, I2, ...). Say
which of those earlier things this new entry changes, and how. The entry may be typed or
a voice transcript: informal, unpunctuated, mid-thought.

There are two ways an entry can change one:

- answers (prayers only): the entry reports how the thing prayed for turned out. It
  happened, it was resolved, the news came. "Got the offer!" answers a prayer about the
  job; "Mum's out of surgery, it went well" answers a prayer for the surgery; "we've
  decided to stay in London, finally at peace" answers a prayer for wisdom about the
  move. An outcome that wasn't the one hoped for still answers it, if the entry says it
  is settled.
  Not an answer: praying about it again, hoping, still waiting ("they'll decide
  Friday"), progress that settles nothing ("viewed three flats, none right"), or the
  same subject mentioned in passing. Gratitude for something else is not an answer to
  this prayer.

- continues (ideas only): the entry develops that same idea. It refines it, extends it,
  changes its mind about it, or starts acting on it. "Thinking more about the
  one-idea-a-morning thing: weekly, not daily" continues an idea about resurfacing one
  old idea each morning; "bought a chair for the reading corner" continues an idea to
  build a reading nook.
  Not a continuation: a different idea about a related subject, or a passing mention.

For each change:
- candidate: its label exactly as listed ("P2", "I1").
- relation: "answers" for a prayer, "continues" for an idea.
- quote: the shortest span of the entry that shows it, copied exactly, at most 200
  characters.
- confidence: your own estimate (0 to 1) that the entry really changes that item. Be
  honest; 0.5 means you are guessing.

Rules:
- Most entries change nothing on the list. An empty list is the usual answer, and a
  good one.
- Use only labels from the list, and each label at most once.
- If you are unsure it is really the same thing, leave it out. A wrong link costs the
  person more than a missed one: they will be asked, and "no" is a chore.
"""


@dataclass(frozen=True)
class Listed:
	"""One earlier item, as the model sees it."""

	label: str  # "P1", "I2"
	title: str
	detail: str | None  # an idea's summary, or who a prayer is for
	written_on: dt.date


def _line(item: Listed) -> str:
	detail = f" - {item.detail}" if item.detail else ""
	return (
		f"{item.label}: {item.title}{detail} (written {item.written_on:%a %-d %b %Y})"
	)


def user_message(
	text: str, listed: list[Listed], *, captured_at: dt.datetime, timezone: str
) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	earlier = "\n".join(_line(item) for item in listed)
	return (
		f"The entry was written at {local.isoformat()} ({local:%A}, timezone "
		f"{timezone}).\n\n<earlier>\n{earlier}\n</earlier>\n\n<entry>\n{text}\n</entry>"
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected list. Keep every change that was valid; fix or drop only "
		"the ones named above."
	)
