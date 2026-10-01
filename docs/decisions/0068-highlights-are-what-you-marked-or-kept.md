# 0068 — Highlights are what you marked or kept; a word in the note marks one

**Status:** active
**Date:** 2026-10-01
**Builds on:** [0015](0015-structured-outputs-and-terminal-validation.md) (the repair loop
this leaves alone), [0030](0030-keep-without-asking.md) (`accepted` means kept without a
look), [0060](0060-highlights-and-titled-names.md) (ideas are called Highlights)
**Revisits:** the idea rule in `extract-v1`, and the Highlights screen's filter

## Problem

Akin: *"the highlights are too loose. Almost every note I have to remove a highlight
from. I need a way to specifically trigger certain things that are highlights."*

Three things made that true at once:

1. **The screen showed guesses.** Highlights listed every idea the extractor produced
   that hadn't been rejected, pending ones included. A model guess was a Highlight the
   moment it existed, and the only way to disagree was to remove it, note by note.
2. **The rule was broad.** "A thought the author would want to find again: an insight,
   a plan, a product idea, a realisation" fits a sentence in most notes.
3. **The gate was loose, and blind.** Idea precision had the lowest bar in the suite
   (0.70), and the golden set's 40 synthetic cases scored 0.96 to 1.00 on it. The eval
   could not see what Akin was seeing because none of his notes were in it.

And there was no way to *say* "this one". The only input was prose, and the reader
decided.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Show only what you kept (confirmed or added); pending ideas wait on the note | Removes the chore at once; a Highlight means something again; app-only | Nothing new appears until it is kept; with auto-accept off that is a tap per real highlight, the same tap as today but "keep" instead of "remove" |
| **An explicit mark in the note** ("note to self", "remember this", "highlight", "idea") | What was asked for: a way to say "this one". Deterministic, so it works the same on every model and is testable without one | A vocabulary to remember; transcripts can mangle it; "idea" has to be guarded against "no idea" and "good idea" |
| Tighten the extractor's idea rule and raise the bar; put Akin's rejections in the golden set | The only option that makes the eval measure his judgement; fewer guesses in review | A prompt version bump and a re-recording; recall of unmarked ideas drops, by design; the real-note cases need his export |
| A confidence threshold | One line | Confidence isn't calibrated (0048 said so; Flash-Lite put 1.0 on everything in 0067). Rejected |
| One call that asks the model "is this a highlight?" per idea | A second opinion | Doubles the cost of the loosest stage to answer a question the person can answer by saying one word. Rejected |

## Decision

**All three of the first options, together.** Marking is code, wording is the model,
and the screen shows only what the person decided.

- **A mark is deterministic** (`services/highlights.py`). "Note to self", "remember
  this", "want to remember", "highlight", "#highlight", "insight", "takeaway", and the
  "idea" family ("an idea", "random idea", "idea:", "idea for the app") mark the
  sentence they sit in, trigger words removed; a trigger alone takes the next sentence;
  a tag on a short note marks the note. "No idea", "good idea", "any idea" mark
  nothing. Five marks at most, 300 characters each. Thirty-odd unit tests pin the
  phrases, because this is the part a person will argue with.
- **The extractor is told what was marked.** The message lists the passages and the
  prompt (`extract-v1.3`) says each is exactly one idea, worded from the passage. After
  the model answers, `ensure_marked` pairs each mark with the idea that best restates
  it (one idea per mark, so nothing doubles) and, if the model gave a mark nothing,
  the passage itself becomes the idea with its first words as the title. **A mark
  always produces one highlight, whatever the model does.**
- **Marked ideas are stored `accepted`**: kept at once, visible in Highlights without a
  tap. `accepted` rather than `confirmed` because extraction is idempotent by replacing
  the unreviewed rows of its version (`pipeline._extract`); a `confirmed` row would
  survive a re-run and the re-run would add a twin. In a reflection (the faith space),
  unmarked ideas are still skipped (0042); a marked one is kept, because the person
  asked.
- **Unmarked ideas are rare.** The rule now says so: an account of the day, a feeling,
  the topic restated, a wish, or something already a todo or event is not an idea;
  when unsure, none. A realisation about oneself still is.
- **Highlights shows confirmed, user-added and accepted ideas.** Pending ones stay on
  their note with Confirm, Edit and Reject, and in the review flow, as before. The
  empty state tells you the words.
- **The gate moved to idea precision 0.90, recall 0.80**, and the golden set gained
  eleven cases: five marked (voice without punctuation, "highlight:", "random idea",
  "remember this" about a person, a tag), one unmarked realisation, and five negatives
  ("no idea", "good idea" as approval, an account of the day, a topic restated, a
  feeling about someone's day).

## What we measured

`extract-v1.3` on `claude-opus-5`, recorded 2026-10-01 through the Railway worker
environment and judged with the Claude matcher (`--match`), 51 cases:

| Kind | Precision | Recall | Bar | v1.2 (40 cases) |
| --- | --- | --- | --- | --- |
| todo | 0.96 | 0.96 | 0.80 / 0.90 | 1.00 / 0.96 |
| event | 1.00 | 1.00 | 0.80 / 0.85 | 1.00 / 1.00 |
| **idea** | **1.00** | **0.93** | **0.90 / 0.80** | 0.89 / 1.00 at the old 0.70 bars |

- **All five marked cases (g41 to g45) produced exactly one idea each**, including the
  voice case with no punctuation and the "remember this" about a person.
- **All five negatives (g46 to g49, g51) produced no idea**: the account of a day, "no
  idea", "good idea" as approval, the topic restated, the feeling about someone's day.
- **No false ideas at all** (precision 1.00); v1.2's g34 duplicate idea is gone.
- **One idea missed, g31**: "I need to stop saying yes to every meeting", unmarked. The
  stricter rule reads it as a wish, the label calls it a realisation. The case stays as
  a label: it is exactly the kind of unmarked thought that may now need a word.
- Todos: g36 unchanged (the folded "compare quotes"), and one new extra todo on g39.
  Both are outside this change's scope and inside the bar.

Before the judge, with lexical title matching only, the same recording read as idea
0.77 / 0.71, which is why the judge exists (paraphrased titles are hits).

## What we're accepting

- **A vocabulary.** Five users will learn five phrases, or say "idea". The empty state
  teaches them; nothing else does.
- **A transcript can drop the mark.** "Note to self" heard as "no to self" marks
  nothing, and the stricter rule then treats the thought as an ordinary sentence. The
  fix is to keep the suggested idea on the note, as before.
- **The fallback title is the passage's first words,** not a model's phrasing, when the
  model ignores a mark. It is correct and plain; the person can edit it.
- **Unmarked recall drops on purpose.** Some real thoughts will no longer be suggested.
  That is the trade Akin asked for, and the one the raised precision bar encodes.
- **The synthetic cases still outnumber the real ones.** Every rejected highlight in
  the app is a labelled false positive; `evals/extraction/harvest.py` turns an export
  into golden cases. Until that runs on Akin's notes, the eval measures Claude's idea
  of looseness, not his.

## Revisit when

- Akin sends an export: harvest it, replace synthetic idea cases with real ones,
  re-record, and re-check the bar.
- A phrase people use isn't in the list, or one in the list fires on ordinary speech
  (watch for `accepted` ideas that get rejected soon after).
- Transcripts miss marks often enough to notice: add the words to Deepgram's keyterms
  (0045 did this for names).
- The Lists feature lands: "I should read X" and "we need Y" stop being ideas at all.

## Interview angle

> **"Users say your extractor is too eager. What do you change?"**

Three things, and the order matters. First the product: stop showing guesses as if
they were decisions; a suggestion waits on the note, a highlight is what the person
kept. Second, give the person a deterministic lever: a phrase in the note that the
code, not the model, recognises, so it works identically on every model and is unit
tested like any parser, and the model's only job for a marked passage is wording. The
code guarantees the passage becomes a highlight even if the model ignores it. Third,
tighten the model's rule and move the gate: raise the precision bar on the error users
see, and feed their rejections back as labelled negatives so the eval measures their
judgement rather than the author's. What I would not do is a confidence threshold
(uncalibrated) or a second model call to judge the first (paying twice to ask a
question the user can answer in a word).
