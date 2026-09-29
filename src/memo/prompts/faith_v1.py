"""The faith prompt (ADR 0042): the prayers in a note. Code like every prompt: versioned
here, stamped on every prayer it produces via FAITH_VERSION, changed only with a bump.

Its own prompt, not a fourth list on the extraction prompt, for the reason the people
prompt is separate: it runs only for someone who has turned Faith on, so extraction
(and its eval) stays exactly as it is for everyone else.
"""

import datetime as dt
from zoneinfo import ZoneInfo

FAITH_VERSION = "faith-v1"

SYSTEM_PROMPT = """\
You read a single entry in one person's private journal and list the things the author
is praying for. The entry may be typed or a voice transcript: informal, unpunctuated,
mid-thought.

What counts: something the author prays, or means to pray, about - a request put to
God, for themselves or for someone else. "Pray for Tunde's mum", "praying for patience
this week", "Lord, give me wisdom about the job", "asking God for strength for Friday",
"I pray the meeting goes well", "Kemi asked me to pray for her exams" (the author has
been asked, so it is now theirs to pray). Something prayed about today counts too:
"prayed for Mum this morning".

What does not count:
- Exclamations and idioms: "oh my God", "thank God the flight landed", "Lord have
  mercy, this traffic", "God knows".
- News of a prayer already answered: "God came through, the offer arrived". That is
  gratitude, not a new request.
- Church logistics, sermons, Bible reading or reflections that ask for nothing: "church
  at 10", "read Romans 8, all things work together".
- Errands that happen to involve the word: "buy a prayer journal".
- Plans to talk to a person: "need to call Tunde" is not a prayer.

For each prayer, once per entry:
- title: what is being prayed for, as a short phrase in the author's own terms, without
  "pray for" in front: "Tunde's mum's surgery on Thursday", "patience with the kids",
  "wisdom about the Lagos job". Specific beats general.
- for_person: the one person it is for, exactly as the entry names them ("Tunde's mum",
  "Kemi", "Dad", "my brother"); null when it is for the author, for a group, or for a
  situation ("the people in the earthquake").
- confidence: your own estimate (0 to 1) that the author means this as a prayer. Be
  honest; 0.5 means you are guessing.

Rules:
- Do not invent. If nothing is being prayed for, return an empty list. That is the
  usual answer, and a good one.
- Two things prayed for are two prayers ("pray about the move and for Ada's new job").
"""


def user_message(text: str, *, captured_at: dt.datetime, timezone: str) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	return (
		f"Captured at {local.isoformat()} ({local.strftime('%A')}, timezone "
		f"{timezone}).\n\n<capture>\n{text}\n</capture>"
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected list. Keep every prayer that was valid; fix or drop only "
		"the ones named above."
	)
