# Lately eval (ADR 0062)

Does the Lately writer (`prompts/lately_v1.py`) say what's been going on with a person,
only what the notes say, and nothing when they say nothing?

One JSON object per line in `golden.jsonl`: `id`, `name`, `notes` (newest first, each
with `on`, optional `in_touch`, `about` and `opening`, as the sweep passes them),
`must` (key facts, each `a|b` for "one of these words"), optional `must_not` (facts a
newer note replaced), optional `empty` (the notes say nothing about them). Written as of
Wednesday 2026-09-30, at the default tone.

| Metric | Bar | Why |
| --- | --- | --- |
| key facts | 0.85 | Picking the second most important thing is fine; the notes are right below. |
| speaks when it should | 0.90 | Empty for a name in a list; a line for anything said about them. |
| no invented names | 1.00 | A name the notes don't have is made up. |
| no guessed pronouns | 1.00 | memo doesn't know anyone's pronouns unless the notes say. |
| no stale facts | 1.00 | "Going in for surgery" after "home and recovering" is wrong, not old. |
| fits | 1.00 | The page has room for about two sentences. |

**A starter set, hand-written by Claude:** news vs time together, a newer note
replacing an older one, a name in a list (empty), a plan involving them, grief, and a
person whose notes use "he". The first recording found three real faults (a guessed
"she", and two small facts treated as nothing), fixed in the prompt before shipping;
its examples were then reworded so none of them is an eval case.

    railway run -s worker -- uv run python -m evals.lately.run --record evals/lately/recorded.json
    uv run python -m evals.lately.run --replay evals/lately/recorded.json   # CI

**Armed (2026-09-30, `lately-v1/claude-opus-5`):** every metric 1.00. As with the other
starter sets, that says the set is too easy; replace it with real people from real
notes, and read the outputs before committing a recording.
