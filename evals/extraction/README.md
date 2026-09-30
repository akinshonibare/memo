# Extraction golden set

`golden.jsonl` — one JSON object per line: `id`, `text`, `captured_at`, `timezone`,
`expected` (an `Extraction`), optional `note` saying what the case is testing.

**This is a starter set, hand-authored by Claude to cover the hard cases** (negation,
already-done, relative dates across a weekend, several items in one sentence, ideas
phrased as imperatives, todos that belong to other people, empty extractions).
Issue #24 asks for ~40 *real* captures. Replace and extend this file with your own as
they accumulate — every PATCH/POST on `/v1/derivations` is a labelled example you can
lift straight in. The thresholds in `scoring.py` are the merge gate; keep them honest.

Run: `uv run python -m evals.extraction.run` (live) or `--replay <file>` (offline).
Record a live run with `--record out.json` to produce an offline replay file for CI.

## Recorded run (2026-09-30, `extract-v1.2/claude-opus-5`, tone plain)

`recorded.json` arms the CI gate; `matches.json` holds a model's judgement of which
predicted item is which expected one (`matching.py`, `match-v1`), so paraphrases score as
hits. Each verdict carries a fingerprint of exactly the items it judged; where that no
longer fits (a label changed, a new recording), scoring falls back to lexical title
matching for that example, and the run prints how many examples used recorded matches.

Re-recorded for v1.2 (no em dashes, summaries in the person's tone; ADR 0061) and
re-judged: todo 1.00/0.96, event 1.00/1.00, idea 0.89/1.00, no em dashes in any output.
Current misses, the same two as v1.1, both real:

- **g34** adds an idea duplicating the todo it also extracted (precision).
- **g36** folds "compare quotes" into the renewal todo's notes instead of a second todo
  (recall).

Re-record after any change to the prompt, schema, or model, then re-judge:

    uv run python -m evals.extraction.run --record evals/extraction/recorded.json
    uv run python -m evals.extraction.run --replay evals/extraction/recorded.json --match

Read the misses before committing either file: a green gate over a stale recording
measures nothing.

## Real captures

    uv run python -m evals.extraction.harvest export.json --timezone <zone> > candidates.jsonl

`export.json` is Settings → Export in the app. A capture is a candidate once its review is
finished; its label is what you kept (confirmed, edited, added). The harvester only
prints: read each candidate before moving it into `golden.jsonl`, since it is your own
text. Then re-record and re-judge. Aim to replace the synthetic cases, not add to them:
the gate should measure your notes, not Claude's idea of them.
