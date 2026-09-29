"""Turn real captures and their reviews into candidate golden cases.

	uv run python -m evals.extraction.harvest export.json --timezone Europe/London \\
		> evals/extraction/candidates.jsonl

`export.json` is Settings → Export in the app (GET /v1/me/export). A capture becomes a
candidate only when the user has finished with it: it has derivations, and none is
still pending_review or auto-accepted (ADR 0030: kept without a look, so not a label).
Mentions (ADR 0029) are not extraction items and are ignored here. Its expected
extraction is exactly what the user kept - confirmed items as they now read, plus
`user_created` ones - so a rejection is a labelled false positive and an addition a
labelled false negative, without anyone writing a label by hand.

Prints candidates; never edits golden.jsonl. Read each one before moving it across:
this is your private text, and a label is only as good as the review behind it.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from memo.schemas.derivations import PAYLOAD_MODELS, Extraction

KEPT = {"confirmed", "user_created"}
UNREVIEWED = {"pending_review", "accepted"}
_LISTS = {"todo": "todos", "event": "events", "idea": "ideas"}


def candidate(capture: dict[str, Any], timezone: str) -> dict[str, Any] | None:
	derivations = [d for d in capture.get("derivations", []) if d["kind"] in _LISTS]
	text = (capture.get("text") or "").strip()
	if not text or capture.get("status") != "ready":
		return None
	if not derivations:
		return None  # nothing was extracted or added: no review, so no label
	if any(d["status"] in UNREVIEWED for d in derivations):
		# Not finished reviewing, or kept without a look: the model's own guesses
		# would become the answers it is graded against.
		return None
	# Everything rejected is a real label too: "there was nothing to extract".
	kept = [d for d in derivations if d["status"] in KEPT]
	expected: dict[str, list[dict[str, Any]]] = {}
	for d in kept:
		model = PAYLOAD_MODELS[d["kind"]]
		item = model.model_validate(d["payload"]).model_dump(
			mode="json", exclude={"confidence"}, exclude_none=True
		)
		expected.setdefault(_LISTS[d["kind"]], []).append(item)
	Extraction.model_validate(expected)  # the golden loader must accept it
	statuses = sorted({d["status"] for d in derivations})
	return {
		"id": f"real-{capture['id'][-12:]}",
		"text": text,
		"captured_at": capture["created_at"],
		"timezone": timezone,
		"expected": expected,
		"note": "harvested from a real capture; reviews: " + ", ".join(statuses),
	}


def main(argv: list[str] | None = None) -> int:
	parser = argparse.ArgumentParser()
	parser.add_argument("export", type=Path)
	parser.add_argument("--timezone", required=True, help="IANA zone the notes were in")
	args = parser.parse_args(argv)
	export = json.loads(args.export.read_text())
	found = 0
	for capture in export.get("captures", []):
		case = candidate(capture, args.timezone)
		if case is not None:
			print(json.dumps(case, ensure_ascii=False))
			found += 1
	print(f"{found} candidates", file=sys.stderr)
	return 0


if __name__ == "__main__":
	sys.exit(main())
