# Lists golden set

`golden.jsonl`: one JSON object per line: `id`, `text`, `lists` (the lists the author
already keeps, as the reader is told), `expected` (items as `match` word + `list`),
optional `note`.

A starter set, hand-authored by Claude to cover the cases that matter: several items in
one breath, quantities, the author's own named lists, recommendations with a title inside
a sentence, and the negatives (already bought or read, todos and events, someone else's
shopping, negations, "list" as a topic). Replace and extend with your own: an item you
remove from a list is a labelled false positive, one you add by hand a false negative.

Run: `uv run python -m evals.lists.run` (live) or `--replay <file>` (offline).
Record a live run with `--record evals/lists/recorded.json` for CI.
