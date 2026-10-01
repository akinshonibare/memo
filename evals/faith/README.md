# Faith eval (ADR 0042)

Does the faith reader (`prompts/faith_v1.py`) find what the author is **praying for**, and
only that? It runs only for someone with Faith turned on, on notes that contain a prayer
word (`services/faith.might_pray`), and files what it finds into Faith, linked to a person
named in the same note and replacing a "Pray for..." todo from that note.

One JSON object per line in `golden.jsonl`: `id`, `text`, `expected` (a list of `match`,
a word the prayer's title or person should contain, and optional `for`, who it's for as
the note names them), optional `note`. Every case is read as captured on Monday
2026-09-28 09:00 Europe/London.

| Metric | Bar | Why |
| --- | --- | --- |
| prayer recall | 0.85 | A missed prayer never reaches Faith. |
| prayer precision | 0.90 | A phantom ("oh my God this traffic") shows up in Faith, and removes the todo it was read from. |
| person accuracy | 0.85 | "Tunde's mum" read as Tunde puts the prayer on the wrong person's page. |

The run also fails if the prayer-word gate would skip a case that has a prayer in it:
the gate is there to save money, never recall.

**This is a starter set, hand-written by Claude** around the hard cases: prayers with no
"pray" in them ("Lord, give me wisdom", "please Jesus let the scan be clear"), being asked
to pray, two prayers in a line, a person named through another ("Bola's dad"), and the
traps - exclamations, "thank God", news of an answered prayer, church logistics, "bless
you" as thanks, an errand with the word in it. Replace it with real notes: every prayer
removed in the app is a precision label, every one added by hand after a note said it is
a recall label.

    uv run python -m evals.faith.run                                   # live
    railway run -s worker -- uv run python -m evals.faith.run --record evals/faith/recorded.json
    uv run python -m evals.faith.run --replay evals/faith/recorded.json # CI

**Armed (2026-09-29, `faith-v1/claude-opus-5`):** every metric 1.00 on the starter set,
about $0.19 for the run. As with people, that says the set is too easy, not that the
reader is perfect. Re-record after any change to the prompt, `FaithExtraction` or the
model, and read the outputs before committing.

**Re-armed on Sonnet (2026-10-01, `faith-v1/claude-sonnet-5`, ADR 0065):** the reader now
runs on `FAITH_MODEL`, Sonnet 5 by default. Four live Sonnet runs scored 1.00 on every
metric each time; `recorded.json` is the fourth, outputs read before committing. To go
back, set `FAITH_MODEL=claude-opus-5` on the worker and re-record with `--model
claude-opus-5`.
