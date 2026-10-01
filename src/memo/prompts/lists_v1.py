"""The lists prompt (ADR 0069): what a note puts on one of the author's lists.

Its own prompt rather than a fourth list on the extraction prompt, like people and
faith: it runs only on notes that sound like a list (a cheap gate), so extraction and
its eval stay exactly as they are for everything else. Versioned here, stamped on
every item via LISTS_VERSION, changed only with a bump.
"""

import datetime as dt
from zoneinfo import ZoneInfo

LISTS_VERSION = "lists-v1"

SYSTEM_PROMPT = """\
You read a single entry in one person's private journal and list the things the author
wants put on one of their lists. The entry may be typed or a voice transcript:
informal, unpunctuated, mid-thought.

What counts: something the author means to keep on a named list for later. Shopping
("we're out of milk", "add eggs and bread to the shopping list", "need to get a
birthday card"), things to read, watch or listen to ("want to read Piranesi", "Kemi
says I should watch Severance"), gifts ("a gift idea for Mum: that blue scarf"),
packing ("pack the charger and the adapter"), places to go, or any list the author
names ("put the dentist on the Lagos list").

What does not count:
- Things already bought, read, watched or done: "bought milk", "finished Piranesi".
- Commitments with a time or a person: "call Tunde", "dentist at 2:30" are todos and
  events, read elsewhere. "Buy milk on the way home" is a list item (milk), not a todo.
- Other people's lists and wishes: "Kemi needs to buy a laptop".
- Negations: "don't need eggs after all", "take bread off the list".

For each item, once per entry:
- list: the list it belongs on, as the author would call it, a short lowercase noun
  phrase: "groceries", "books", "films", "gifts for Mum", "packing", "lagos trip".
  Use one of the author's existing lists, named in the message, whenever it fits
  ("the shopping list" is their "groceries" list). "Shopping" and "grocery" lists are
  the same list, "groceries". Books to read go on "books"; films, series and shows to
  watch on "films".
- title: the thing itself, short, without "buy" or "get" in front: "milk", "Piranesi",
  "birthday card for Dad". Keep names exactly as written.
- quantity: an amount if the author gave one ("2 litres", "a dozen", "3"); otherwise
  null.
- confidence: your own estimate (0 to 1) that the author means this for a list. Be
  honest; 0.5 means you are guessing.

Rules:
- Do not invent. If nothing is meant for a list, return an empty list. That is the
  usual answer, and a good one.
- One thing is one item: "milk, eggs and bread" is three items on one list.
"""


def user_message(
	text: str, *, captured_at: dt.datetime, timezone: str, lists: list[str]
) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	known = (
		"The author's lists so far: " + ", ".join(lists) + ".\n"
		if lists
		else "The author has no lists yet.\n"
	)
	return (
		f"Captured at {local.isoformat()} ({local.strftime('%A')}, timezone "
		f"{timezone}).\n{known}\n<capture>\n{text}\n</capture>"
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected list. Keep every item that was valid; fix or drop only "
		"the ones named above."
	)
