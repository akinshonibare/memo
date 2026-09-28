Raw extractor outputs in the exact shape the API returns under the `Extraction`
JSON-schema constraint. Hand-authored by Claude until a live run replaces them:
`uv run python -m evals.extraction.run --record tests/fixtures/extraction/live.json`
once `ANTHROPIC_API_KEY` is set. Tests parse these through the same path production
uses, so a schema change that breaks parsing fails here, not in the worker.
