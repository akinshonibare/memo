"""The habits prompt, v2 (ADRs 0043, 0050): what the author actually did, in a note, and
now the numbers they gave for it - how far, how long, how heavy - so memo can notice a
milestone ("first 10k", "heaviest squat yet") without anyone logging a workout.

Code like every prompt: versioned here, stamped on every activity it produces via
HABITS_VERSION, and changed only with a version bump. v1 is kept for the record.
"""

from memo.prompts.habits_v1 import KEYS, repair_message, user_message

HABITS_VERSION = "habits-v2"

__all__ = ["HABITS_VERSION", "KEYS", "SYSTEM_PROMPT", "repair_message", "user_message"]

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
- amounts: the numbers the author gave for this activity, if any. Usually none: return
  an empty list unless the entry states a number for it.
  - measure "distance": how far, for a run, walk, ride or swim. "5k" is 5 km, "a half
    marathon" is 21.1 km, "a marathon" is 42.2 km. Swimming lengths are not a
    distance unless the entry says how long the pool is.
  - measure "duration": how long it took or lasted: "30 minutes of yoga", "10k in 52
    minutes" (that run has a distance and a duration).
  - measure "weight": the heaviest weight lifted for one lift, with `lift` the lift's
    plain name, lowercase and singular ("squat", "deadlift", "bench press"). One amount
    per lift. Not body weight: "I weigh 80kg" is not a lift.
  - unit: exactly as the author used it: km, mi, m, min, h, kg or lb. Copy the number;
    do not convert it. "10 miles" is value 10, unit "mi". "An hour and a half" is 1.5 h.
  - lift: the lift, for a weight; an empty string for a distance or a duration.
  - Only numbers the author stated or named ("5k", "a marathon"). Never estimate one:
    "a long run" has no distance; "a quick session" has no duration. Pages read, sets,
    reps and calories are not amounts.

Rules:
- Do not invent. If nothing qualifies, return an empty list. That is a common answer.
- Reading the Bible is key "bible", not "reading".
- Two different activities are two entries; the same one mentioned twice is one. The
  same activity on different days is one entry per day ("gym Monday and Wednesday" is
  two).
"""
