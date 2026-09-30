"""The Lately prompt (ADR 0062): a line or two, at the top of a person's page, on
what's been going on with them, from what the author's notes said about them. Code
like every prompt: versioned here, stamped on the person row, changed only with a bump.
"""

import datetime as dt
from dataclasses import dataclass

from memo.prompts.voice import writing_rules

LATELY_VERSION = "lately-v1"
MAX_CHARS = 220

SYSTEM_PROMPT = f"""\
You write "Lately": one or two short sentences at the top of a person's page in
someone's private journal app, saying what has been going on with that person
recently. You work only from what the journal's author wrote about them.

You get the person's name, today's date, and the notes that mention them, newest
first. Each has its date, whether the author was in touch with them, what the note
says about them, and the note's opening words.

Write:
- What's been going on with them lately: news about them (a new job, a move, a worry,
  a celebration) and what the author did with them. The most recent and the most
  significant first. Older notes only matter if nothing newer replaces them.
- The author is "you". Call the person by their first name. Never "he", "she",
  "him", "her" or "his" unless the notes themselves use that word for them; if you
  need to refer to them twice, use their name again.
- At most {MAX_CHARS} characters, one or two sentences. No list, no heading, no date
  stamps; "on Monday" or "last week" when it helps, worked out from today's date.

Never:
- Say anything the notes don't. No guessing, no advice, no feelings they don't state,
  nothing from general knowledge.
- Pad a thin record. Anything the notes say about them, or plan with or for them,
  counts, however small: "Sam is hosting book club on Friday", "you owe Lara a call
  back". Only when they are just a name in a list, with nothing said about them,
  return an empty text. That is a good answer then.
"""


@dataclass(frozen=True)
class Mentioned:
	on: dt.date
	in_touch: str | None  # "spoke on the phone", "in person", or None for a mention
	about: str | None  # what the note says about them (the people reader's context)
	opening: str | None  # the note's first words


def system(tone: str | None) -> str:
	return f"{SYSTEM_PROMPT}\n{writing_rules(tone)}\n"


def user_message(name: str, today: dt.date, notes: list[Mentioned]) -> str:
	lines = [f"Person: {name}", f"Today: {today.strftime('%A %-d %B %Y')}", ""]
	for n in notes:
		head = n.on.strftime("%a %-d %b")
		touch = f" · {n.in_touch}" if n.in_touch else " · mentioned"
		lines.append(f"- {head}{touch}")
		if n.about:
			lines.append(f"  About them: {n.about}")
		if n.opening:
			lines.append(f"  The note begins: {n.opening}")
	return "\n".join(lines)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		f"Return the corrected line: at most {MAX_CHARS} characters, or empty."
	)
