"""The answer prompt, v2 (ADR 0021). v1 saw only the retrieved chunk text, so a note
saying "next Tuesday" stayed "next Tuesday" even after extraction had resolved it to a
date and the user had confirmed it. v2 gives the model today's date and, under each
note, what was extracted from it, read live at answer time with its review status."""

import datetime as dt
from dataclasses import dataclass, field
from typing import Any
from zoneinfo import ZoneInfo

# v2.1: a done todo is marked "done" in its status (the Planner's checkbox).
ANSWER_VERSION = "answer-v2.1"

SYSTEM_PROMPT = """\
You answer questions about one person's private journal, for that person. The only
things you know about their life are the numbered notes provided in the message. Each
note has the date it was written. Today's date is given above the notes.

Some notes list what was extracted from them: todos, events, and ideas, with dates
already worked out. Each is marked:
- "confirmed": the person checked it. Trust its date over a relative phrase in the note.
- "added by you": the person wrote it themselves. Trust it the same way.
- "unreviewed": an automatic reading of the note. Use it, but say it is unconfirmed if
  the answer depends on it.

Rules:
- Answer only from the notes. If they do not contain the answer, say so plainly -
  "your notes don't mention that" - and do not guess or fill in from general knowledge.
- When you use a note or something extracted from it, refer to it inline by the note's
  number in square brackets, like [2]. Use only numbers that exist in the list.
- Be brief and direct; this is a personal assistant, not an essay. Dates matter: give
  the actual date ("Tuesday 6 October") rather than repeating "next Tuesday", and use
  today's date to say whether something is past or still to come.
- Never contradict a note. If notes disagree, say that they disagree and cite both.
"""

_STATUS = {
	"confirmed": "confirmed",
	"user_created": "added by you",
	"pending_review": "unreviewed",
}


@dataclass(frozen=True)
class Note:
	number: int
	written_at: dt.datetime
	text: str
	extracted: list[str] = field(default_factory=list)


def _when(iso: str, zone: ZoneInfo) -> str:
	moment = dt.datetime.fromisoformat(iso).astimezone(zone)
	return moment.strftime("%a %-d %b %Y, %H:%M")


def extracted_line(
	kind: str,
	payload: dict[str, Any],
	status: str,
	zone: ZoneInfo,
	*,
	done: bool = False,
) -> str:
	"""One derivation as the model sees it: `event: Dentist · Tue 6 Oct 2026, 12:00`."""
	parts = [f"{kind}: {payload.get('title', '')}"]
	if kind == "todo" and payload.get("due"):
		due = dt.date.fromisoformat(payload["due"])
		parts.append(f"due {due.strftime('%a %-d %b %Y')}")
	if kind == "event" and payload.get("starts_at"):
		parts.append(_when(payload["starts_at"], zone))
		if payload.get("location"):
			parts.append(f"at {payload['location']}")
	for key in ("summary", "notes"):
		if payload.get(key):
			parts.append(str(payload[key]))
	label = _STATUS.get(status, status)
	return f"{' · '.join(parts)} ({label}{', done' if done else ''})"


def notes_block(notes: list[Note], zone: ZoneInfo) -> str:
	if not notes:
		return "<notes>\n(no relevant notes were found)\n</notes>"
	blocks = []
	for note in notes:
		written = note.written_at.astimezone(zone).date().isoformat()
		block = f"[{note.number}] ({written}) {note.text.strip()}"
		if note.extracted:
			block += "\n  Extracted:\n" + "\n".join(f"  - {e}" for e in note.extracted)
		blocks.append(block)
	return "<notes>\n" + "\n\n".join(blocks) + "\n</notes>"


def user_turn(
	question: str, notes: list[Note], *, today: dt.date, zone: ZoneInfo
) -> str:
	return (
		f"Today is {today.strftime('%A %-d %B %Y')}.\n\n"
		f"{notes_block(notes, zone)}\n\nQuestion: {question}"
	)
