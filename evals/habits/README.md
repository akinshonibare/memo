# Habits eval (ADR 0043)

Does the habits reader (`prompts/habits_v1.py`) find what the author actually **did**, on
the day they did it, and nothing they only planned? It runs on every note of someone
with Habits turned on, and each activity it finds counts a day towards a habit.

One JSON object per line in `golden.jsonl`: `id`, `text`, `expected` (a list of `key`,
the key the activity should be filed under, `on`, the date it happened, and optional
`or`, other keys that are also right), optional `extra_keys` (habits the author named,
given to the reader as in the app) and `note`. Every case is read as captured on Monday
2026-09-28 09:00 Europe/London. Scored as the app counts: one day per (key, date).

| Metric | Bar | Why |
| --- | --- | --- |
| activity precision | 0.95 | A false count ("gym tomorrow" filed as a gym day) makes the numbers look better than the truth, and nothing looks wrong. The costly error, so the highest bar. |
| activity recall | 0.85 | A missed day is visible (the week looks quieter than it felt) and one tap to log. |
| date accuracy | 0.90 | "Yesterday" filed as today moves a day between weeks. |

**This is a starter set, hand-written by Claude** around the hard cases: activities with
no obvious word ("5k", "leg day", "five-a-side"), last night and last Wednesday resolved
from a Monday, several days in one note, two activities in one line, Bible reading kept
apart from reading, a habit the author named themselves, and the traps - plans, wishes,
negations ("skipped the gym", "too lazy to cook"), someone else's habit, errands with the
word in them ("renew the gym membership"), a commute, a call with Mum (People reads
that), and "on the way to spin class". Replace it with real notes: every day removed in
the app is a precision label, every day logged by hand after a note said it is a recall
label.

    uv run python -m evals.habits.run                                    # live
    railway run -s worker -- uv run python -m evals.habits.run --record evals/habits/recorded.json
    uv run python -m evals.habits.run --replay evals/habits/recorded.json # CI

Re-record after any change to the prompt, `HabitsExtraction` or the model, and read the
outputs before committing.
