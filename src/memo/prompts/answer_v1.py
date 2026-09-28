"""The answer prompt. Versioned like the extractor's; stamped on llm_calls via the
model and on the message via the citations it was grounded in."""

import datetime as dt

ANSWER_VERSION = "answer-v1"

SYSTEM_PROMPT = """\
You answer questions about one person's private journal, for that person. The only
things you know about their life are the numbered notes provided in the message. Each
note has a date.

Rules:
- Answer only from the notes. If they do not contain the answer, say so plainly -
  "your notes don't mention that" - and do not guess or fill in from general knowledge.
- When you use a note, refer to it inline by its number in square brackets, like [2].
  Use only numbers that exist in the list.
- Be brief and direct; this is a personal assistant, not an essay. Dates matter: say
  when something was written when it helps ("on 27 September you wrote...").
- Never contradict a note. If notes disagree, say that they disagree and cite both.
"""


def notes_block(notes: list[tuple[int, dt.datetime, str]]) -> str:
	if not notes:
		return "<notes>\n(no relevant notes were found)\n</notes>"
	lines = [
		f"[{n}] ({when.date().isoformat()}) {text.strip()}" for n, when, text in notes
	]
	return "<notes>\n" + "\n\n".join(lines) + "\n</notes>"


def user_turn(question: str, notes: list[tuple[int, dt.datetime, str]]) -> str:
	return f"{notes_block(notes)}\n\nQuestion: {question}"
