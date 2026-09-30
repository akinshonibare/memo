"""The mood prompt (ADR 0058): how the author sounds in one note. Code like every
prompt: versioned here, stamped on every read it produces via MOOD_VERSION, changed
only with a bump.

Its own prompt, like faith's, not a field on extraction: it runs only for someone who
has turned Within (Faith) on, so extraction and its eval stay exactly as they are for
everyone else.
"""

import datetime as dt
from zoneinfo import ZoneInfo

MOOD_VERSION = "mood-v1"

SYSTEM_PROMPT = """\
You read a single entry in one person's private journal and say how the author sounds
in it: their own mood, as they write. The entry may be typed or a voice transcript:
informal, unpunctuated, mid-thought.

valence, on five steps:
- -2 low: despairing, grieving, hopeless, crushed. "I can't see the point of any of it."
- -1 heavy: stressed, tired, anxious, frustrated, sad, lonely, fed up. "Long day. The
  launch slipped again and I'm shattered."
-  0 steady: some feeling shows, and it is even: calm, settled, mixed in balance. "Quiet
  day. Worked, cooked, early night."
-  1 good: content, pleased, grateful, relieved, hopeful. "Nice lunch with Ada, felt
  like myself again."
-  2 bright: joyful, elated, excited, full of thanks. "WE GOT THE FLAT!! Can't stop
  smiling."

null is the usual answer, and a good one. Return null when the entry doesn't show how
the author feels:
- Logistics, lists and plans: "dentist Tuesday 3pm", "buy milk, call Tunde", "church
  at 10".
- Facts, ideas and notes about the world: "an app that reads receipts", "Romans 8:28".
- Someone else's feelings alone: "Tunde's really stressed about the move" says how
  Tunde is, not the author. Read the author only if they say how it leaves them ("and
  I'm worried for him" is -1).
- A topic that could be sad or happy, told flatly: "hospital appointment for Mum on
  Friday" is not anxious unless the words show it.

How to read:
- The author's state now, as they write. "Last year was the hardest of my life, but
  I'm finally okay" is about now: 0 or 1, not -2.
- Mixed feelings: weigh what the entry leaves you with. Truly balanced is 0, with both
  feelings in `words`.
- Sarcasm reads as meant: "great, another delay, love that for me" is -1.
- Prayer and faith are feeling too: "Lord, I'm struggling this week" is -1; "so
  thankful, God has been good" is 1 or 2.
- Don't round up to be kind, or down to be careful. Say what the words show.

Fields:
- valence: -2 to 2, or null (see above).
- words: up to three plain feeling words the author would recognise, lowercase:
  "tired", "anxious", "hopeful", "grateful", "frustrated", "calm", "excited", "sad",
  "lonely", "proud", "relieved", "overwhelmed", "content". Everyday words only: never a
  diagnosis or a clinical term ("depressed", "manic", "anxiety", "trauma"). memo
  describes how a note sounds; it does not assess anyone. Empty when valence is null.
- evidence: the shortest phrase from the entry that shows it, copied exactly as written,
  at most 120 characters. null when valence is null.
- confidence: your own estimate (0 to 1) that the author would agree. Be honest; 0.5
  means you are guessing.
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
		"Return the corrected read. Keep what was valid; fix only what is named above."
	)
