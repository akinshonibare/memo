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
