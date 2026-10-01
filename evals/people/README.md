# People evals (ADR 0029)

Two evals, because the feature has two halves with different failure costs.

## Reading: `golden.jsonl`, `run.py`, `scoring.py`

Does the model find the people in a note, and tell **being in touch** from **being
mentioned**? One JSON object per line: `id`, `text`, `expected` (a list of `name`,
`contact`, optional `occurred_on`), optional `note`. Every case is read as captured on
Monday 2026-09-28 09:00 Europe/London, by "Akin".

| Metric | Bar | Why |
| --- | --- | --- |
| person recall | 0.85 | A missed person is a note missing from their page. |
| person precision | 0.85 | An invented person is a phantom in the list, one tap to reject. |
| **contact precision** | **0.95** | "Need to call Tunde" read as a call moves Tunde's last contact forward, so the reminder never comes and nothing looks wrong. The silent failure. |
| contact recall | 0.85 | A missed contact makes a reminder come early: visible, one tap. |
| date accuracy | 0.85 | Among real contacts, the resolved date. |

At 22 cases, 0.95 contact precision allows **zero** false contacts. That is deliberate.

**This is a starter set, hand-written by Claude to cover the hard cases** (plans and
intentions, news heard second-hand, public figures, the author's own name, groups,
relative weekdays). Replace it with real notes as they accumulate.

    uv run python -m evals.people.run                                    # live
    uv run python -m evals.people.run --record evals/people/recorded.json
    uv run python -m evals.people.run --replay evals/people/recorded.json # CI

**Armed (2026-09-29, `people-v1/claude-opus-5`):** `recorded.json` is a live run, and CI
replays it. Every metric scored 1.00 on the starter set, which says the set is too easy,
not that the reader is perfect: real notes will find its misses. Re-record after any
change to `prompts/people_v1.py`, `PeopleExtraction`, or the model, and read the misses
before committing. Railway's worker holds the key, so no local `.env` is needed:

    railway run -s worker -- uv run python -m evals.people.run --record evals/people/recorded.json

## Resolving: `resolution.jsonl`, `resolution.py`

Which person does a name mean? Deterministic, so it runs in CI with nothing recorded.
Each case lists the people memo already knows, one mention, and the right outcome:
`link` (to whom), `ask` (about whom), or `new`. Every case must come out as labelled,
and failures are reported by what they cost:

- **wrong link**: two people's histories silently merged. The gate exists for this.
- **split**: a new person who already existed. One merge fixes it.
- **needless question**: asked when the answer was clear. One tap.

Two known splits are labelled as such on purpose (r12 "Dr Okafor", r13 "T"). Titles
are kept because stripping "Uncle" would merge "Uncle Tunde" into your friend Tunde.
Resolving that is a merge in the app, which then teaches memo the alias.

    uv run python -m evals.people.resolution

Every "which one?" you answer in the app is a labelled case. Add real ones here.

**Re-armed on Sonnet (2026-10-01, `people-v1/claude-sonnet-5`, ADR 0065):** the reader now
runs on `PEOPLE_MODEL`, Sonnet 5 by default. Four live Sonnet runs scored 1.00 on every
metric each time; `recorded.json` is the fourth, outputs read before committing. To go
back, set `PEOPLE_MODEL=claude-opus-5` on the worker and re-record with `--model
claude-opus-5`.
