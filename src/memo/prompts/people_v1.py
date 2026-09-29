"""The people prompt (ADR 0029). Code like every prompt: versioned here, stamped on
every mention row via PEOPLE_VERSION, changed only with a version bump.

Its own prompt, not three more fields on the extraction prompt, for two reasons: the
people stage has to run over notes written before it existed without re-extracting
(and duplicating) their todos, and the extraction eval stays exactly as it was.
"""

import datetime as dt
from zoneinfo import ZoneInfo

PEOPLE_VERSION = "people-v1"

SYSTEM_PROMPT = """\
You read a single entry in one person's private journal and list the people in it.
The entry may be typed or a voice transcript: informal, unpunctuated, mid-thought.

Who counts: people the author knows or deals with personally - family, friends,
colleagues, neighbours, acquaintances - named ("Tunde", "Dr Okafor") or referred to
in a way that picks out one specific person ("Mum", "my brother", "my manager").
Not the author. Not public figures, authors or celebrities the author has no contact
with. Not businesses, and not unnamed roles ("the plumber", "a guy at the gym").

For each person, once per entry even if they come up several times:
- name: exactly as written ("Tunde", "Mum", "Dr Okafor"). Preserve spelling.
- full_name: only when the entry gives more than `name` ("Tunde Bakare"); else null.
- hint: a few words from the entry that would tell this person apart from someone
  else with the same name ("from work", "my cousin", "Ada's husband"); else null.
- contact: whether the author was actually in touch with them, in the past:
  - in_person: met, saw, had lunch with, visited.
  - call: a phone or video call.
  - message: texted, emailed, messaged, wrote to.
  - unspecified: they spoke or were in touch, but how is not clear ("spoke to Tunde").
  - none: only mentioned. Heard about them from someone else, thought about them,
    plans to see or call them, or something they did without the author. A future
    plan is always none, even a firm one; so is "I need to call Tunde".
- occurred_on: for any contact other than none, the date it happened (YYYY-MM-DD),
  resolved against the capture time given in the message ("yesterday", "on
  Saturday"). If the entry does not say when, use the capture date. Null for none.
- context: a short phrase, in the author's sense, of what the entry says about this
  person or what was talked about. It is shown back to the author later as "last
  time: ...", so never name or refer to the author ("dinner on Friday", not "Author
  plans dinner with her on Friday"). Specific beats general: "started the new job at
  Flutterwave", not "talked about work".
- confidence: your own estimate (0 to 1) that this is a real person the author knows
  and that `contact` is right. Be honest; 0.5 means you are guessing.

Rules:
- Do not invent. If nobody qualifies, return an empty list. That is a good answer.
- A group ("the team", "my parents") is not a person unless members are named; list
  named members individually.
- The author's own name, if given, is the author: never list them.
"""


def user_message(
	text: str, *, captured_at: dt.datetime, timezone: str, author: str | None
) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	weekday = local.strftime("%A")
	who = f"The author is {author}. " if author else ""
	return (
		f"{who}Captured at {local.isoformat()} ({weekday}, timezone {timezone}). "
		"Resolve relative dates against this moment.\n\n"
		f"<capture>\n{text}\n</capture>"
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected list. Keep every person that was valid; fix or drop "
		"only the ones named above."
	)
