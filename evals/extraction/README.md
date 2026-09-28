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

## Recorded run (2026-09-28, `extract-v1.1/claude-opus-5`)

`recorded.json` arms the CI gate. Known misses, so they aren't mistaken for regressions:

- **Matcher artifacts, not model errors:** g14 ("Commit before enqueue as the durability
  boundary" vs "Write path should commit before enqueuing") and g40 ("Get the eval set
  done" vs "Finish the eval set"). Title token matching can't see these paraphrases. The
  fix is a semantic matcher (an LLM judge with recorded verdicts), not a looser threshold.
- **Real misses:** g34 adds an idea duplicating the todo it also extracted (precision);
  g36 folds "compare quotes" into the renewal todo's notes instead of a second todo (recall).

Re-record after any change to the prompt, schema, or model, and read the misses before
committing the file: a green gate over a stale recording measures nothing.
