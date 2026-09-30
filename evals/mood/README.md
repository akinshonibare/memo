# Mood eval (ADR 0058)

Does the mood reader (`prompts/mood_v1.py`) hear how the **author** sounds in a note,
only when the note shows it, and never the wrong way round? It runs for someone with
Within (Faith) on, on every note with words, and its reads become the dots on the Within
screen.

One JSON object per line in `golden.jsonl`: `id`, `text`, `valence` (the best answer,
-2 to 2, or null when the note shows no mood), optional `accept` (every answer that is
also right, null included), optional `note`. Every case is read as captured on Monday
2026-09-28 21:30 Europe/London.

| Metric | Bar | Why |
| --- | --- | --- |
| mood recall | 0.85 | A missed mood is a quiet dot: visible, and cheap. |
| mood precision | 0.90 | A mood on a to-do list puts a feeling on a day that said nothing. |
| valence accuracy | 0.80 | The step is often arguable; `accept` holds the arguable ones. |
| within one | 0.95 | Heavy read as low is forgivable; two steps off is not. |
| no sign flip | 1.00 | A grieving note read as good would end trust in the screen. |
| evidence quoted | 0.90 | The day sheet shows the phrase; a paraphrase puts words in the author's mouth. |

The schema also refuses clinical words ("depressed", "anxiety") and a valence without
evidence, so those never reach scoring: the reader has to repair them first.

**This is a starter set, hand-written by Claude** around the hard cases: someone else's
feeling ("Tunde's really stressed"), sarcasm, prayer as feeling, grief, a hard topic told
flatly ("hospital appointment for Mum"), mixed feelings, good news that doesn't land ("I
should be happy about the offer"), and the usual answer, null, for logistics and ideas.
Replace it with real notes: every day you correct in Within is a label.

    uv run python -m evals.mood.run                                   # live
    railway run -s worker -- uv run python -m evals.mood.run --record evals/mood/recorded.json
    uv run python -m evals.mood.run --replay evals/mood/recorded.json # CI

**Armed (2026-09-30, `mood-v1/claude-opus-5`):** every metric 1.00 except evidence
quoted, 0.96 (m36 joined two phrases with "..."; the app drops such a quote and keeps
the read). As with faith, that says the set is too easy, not that the reader is
perfect. Re-record after any change to the prompt, `MoodRead` or the model, and read the
outputs before committing.
