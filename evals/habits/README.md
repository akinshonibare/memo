# Habits eval (ADR 0043)

Does the habits reader (`prompts/habits_v2.py`) find what the author actually **did**, on
the day they did it, and nothing they only planned - and, since v2 (ADR 0050), the numbers
the note gives for it, and no number it doesn't? It runs on every note of someone
with Habits turned on, and each activity it finds counts a day towards a habit.

One JSON object per line in `golden.jsonl`: `id`, `text`, `expected` (a list of `key`,
the key the activity should be filed under, `on`, the date it happened, optional `or`,
other keys that are also right, and `amounts`, the numbers the note gives in km, minutes
or kg - empty when it gives none, `null` when what a number belongs to is ambiguous), optional `extra_keys` (habits the author named,
given to the reader as in the app) and `note`. Every case is read as captured on Monday
2026-09-28 09:00 Europe/London. Scored as the app counts: one day per (key, date).

| Metric | Bar | Why |
| --- | --- | --- |
| activity precision | 0.95 | A false count ("gym tomorrow" filed as a gym day) makes the numbers look better than the truth, and nothing looks wrong. The costly error, so the highest bar. |
| activity recall | 0.85 | A missed day is visible (the week looks quieter than it felt) and one tap to log. |
| date accuracy | 0.90 | "Yesterday" filed as today moves a day between weeks. |
| amount precision | 0.95 | An invented number ("a long run" as 15 km) is a false "Longest run yet": the same silent failure as a false count. |
| amount recall | 0.80 | A missed number is a milestone not noticed; the day still counts. |

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

**Armed (2026-09-29, `habits-v1/claude-opus-5`):** every metric 1.00 on the starter set,
about $0.30 for the run. Every trap came back empty. As with faith and people, that says
the set is too easy, not that the reader is perfect: real notes will be messier. Re-record
after any change to the prompt, `HabitsExtraction` or the model, and read the outputs
before committing.

**Re-armed (2026-09-29, `habits-v2/claude-opus-5`, ADR 0050):** 44 cases, 12 of them new
and about numbers - miles and pounds copied and converted in code, a distance and a time
on one run, two lifts in one note, a named half marathon - and traps: "a long run", a
"quick session", calories, body weight, a time of day, pages, and a plan with a number
in it. Every metric 1.00, about $0.62 for the run. Outputs read before committing.

**Re-armed on Sonnet (2026-10-01, `habits-v2/claude-sonnet-5`, ADR 0065):** the reader now
runs on `HABITS_MODEL`, Sonnet 5 by default. Four live Sonnet runs scored 1.00 on every
metric each time; `recorded.json` is the fourth, outputs read before committing. To go
back, set `HABITS_MODEL=claude-opus-5` on the worker and re-record with `--model
claude-opus-5`.
