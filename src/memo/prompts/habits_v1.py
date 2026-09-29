"""The habits prompt (ADR 0043): what the author actually did, in a note. Code like
every prompt: versioned here, stamped on every activity it produces via HABITS_VERSION,
and changed only with a version bump.

Its own prompt, like people and faith: it runs only for someone with Habits on, so
extraction and its eval stay exactly as they are for everyone else.
"""

import datetime as dt
from zoneinfo import ZoneInfo

HABITS_VERSION = "habits-v1"

# The keys activities are filed under. A fixed vocabulary, because noticing a habit
# means seeing the same thing repeat: "gym", "leg day" and "weights" land on one key.
KEYS: dict[str, str] = {
	"gym": "a gym session, weights, a workout class, any strength or general workout",
	"running": "a run or a jog",
	"walking": "a walk taken for its own sake (not walking to get somewhere)",
	"cycling": "a bike ride for exercise",
	"swimming": "a swim",
	"yoga": "yoga or pilates",
	"sport": "playing a sport: football, tennis, basketball, climbing",
	"reading": "reading a book or long-form writing (not emails, messages or the news)",
	"bible": "reading the Bible, or a Bible study",
	"prayer": "time spent praying",
	"journalling": "writing in a journal or diary",
	"meditation": "meditating",
	"cooking": "cooking a proper meal at home",
	"music": "practising or playing an instrument, singing practice",
	"study": "studying, a course, language practice",
	"early night": "going to bed early on purpose",
}

_KEY_LINES = "\n".join(f"- {key}: {what}" for key, what in KEYS.items())

SYSTEM_PROMPT = f"""\
You read a single entry in one person's private journal and list the things the author
actually DID that could be a habit: exercise, reading, spiritual practice, creative
practice, learning, rest. The entry may be typed or a voice transcript: informal,
unpunctuated, mid-thought.

Only what was done. Not:
- plans or intentions: "gym tomorrow", "I should go for a run", "want to read more";
- wishes, advice or other people's activities: "Sam goes running every day";
- negations: "skipped the gym", "didn't read tonight", "too tired to run";
- routine logistics and errands: commuting, shopping, chores, work tasks, meetings;
- time with people: talking to, meeting, calling or texting someone is read elsewhere.
  (Doing an activity with someone still counts: "ran with Sam" is running.)

For each activity:
- key: one of these when it fits, exactly as written:
{_KEY_LINES}
  Otherwise a short lowercase noun of one or two words for the activity ("gardening",
  "painting"). Prefer a listed key: "leg day" is gym, "5k" is running.
- label: the activity in the author's words, short ("leg day", "30 pages of Atomic
  Habits").
- occurred_on: the date it happened (YYYY-MM-DD), resolved against the capture time
  given in the message ("yesterday", "on Saturday"); the capture date when the entry
  does not say.
- confidence: your own estimate (0 to 1) that the author did this. Be honest.

Rules:
- Do not invent. If nothing qualifies, return an empty list. That is a common answer.
- Reading the Bible is key "bible", not "reading".
- Two different activities are two entries; the same one mentioned twice is one. The
  same activity on different days is one entry per day ("gym Monday and Wednesday" is
  two).
"""


def user_message(text: str, *, captured_at: dt.datetime, timezone: str) -> str:
	try:
		local = captured_at.astimezone(ZoneInfo(timezone))
	except (KeyError, ValueError):
		local = captured_at
		timezone = "UTC"
	return (
		f"Captured at {local.isoformat()} ({local.strftime('%A')}, timezone "
		f"{timezone}). Resolve relative dates against this moment.\n\n"
		f"<capture>\n{text}\n</capture>"
	)


def repair_message(errors: str) -> str:
	return (
		"Your previous answer did not pass validation:\n"
		f"{errors}\n\n"
		"Return the corrected list. Keep every activity that was valid; fix or drop "
		"only the ones named above."
	)
