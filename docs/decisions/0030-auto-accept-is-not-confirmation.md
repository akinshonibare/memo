# 0030 — Auto-accept: keep what extraction finds without asking, stored as `accepted`

**Status:** active
**Date:** 2026-09-29

## Problem

Akin asked for a setting to keep extracted items without confirming each one, off by
default. Every todo, event and idea already shows up in Today and the Planner before
review. So the setting removes the *asking*: the Confirm / Reject buttons, "to review"
labels, and Ask saying "unconfirmed".

The real question is what such an item *is*. Two things already depend on "confirmed"
meaning "a person checked this":

- **Ask** (ADR 0021) trusts a confirmed date over a relative phrase in the note, and
  hedges on unreviewed ones.
- **The eval harvester** turns confirmed items into golden labels. That is how real notes
  replace the synthetic extraction set (handoff item 4).

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| Store them as `confirmed` (the obvious one) | No new status; one line in the extract stage | A lie that other code believes. The harvester would turn the model's own output into labels, so the model would be graded against its own guesses and the extraction gate would drift towards whatever the model already does. Ask would treat a guess as a checked fact. |
| Hide the review UI on the device, keep `pending_review` | No server change | Ask keeps hedging on everything; every other device and every server-side capture (Siri, share sheet) still reads as unreviewed; the setting lives on one phone. |
| **A new status, `accepted`, set by the extract and people stages when the user's setting is on** | Honest: kept, shown, trusted by Ask, never a label. Editing one makes it `confirmed`, because then a person has looked at it. | A fifth status in the vocabulary: a migration, the app's types, Ask's labels, the harvester, and every `status IN (...)` query have to know it. |

## Decision

**`users.auto_accept`, off by default. When on, extraction writes items as `accepted`
and a linked mention as `accepted`. A name that could be two people still waits for
"which one?".**

- **Ask** (`answer-v2.3`, unreleased with ADR 0029) labels them "accepted automatically"
  and uses them without hedging.
- **The harvester** treats `accepted` like `pending_review`: a note with one isn't
  finished, so it is never a candidate.
- **An edit** of an accepted item makes it `confirmed`, as an edit of a pending one does.
- **Turning the setting on** applies from the next note. What already waits keeps
  waiting: a switch about the future is not a review of the past.
- It is an account setting on the server, not a device one, so notes that arrive
  through Siri or the share sheet follow it too.

## What we're accepting

- **The eval intake starves while it is on.** Real notes only become golden cases when
  someone reviews them. With auto-accept on, only edited, rejected or added items produce
  labels. The extraction gate keeps running on the cases it has, but stops learning from
  new notes. That is the honest cost of not reviewing, and it is why the setting is off
  by default.
- **Mistakes stay until noticed.** An invented todo now sits in the Planner, marked
  "kept automatically", until you remove it. It is still visible, never silent (the
  brief's rule), but nothing prompts you to look.
- **One more status to remember** wherever code filters on status. `UNREVIEWED` in
  `schemas/derivations.py` names the pair that means "no person has looked at this".

## Revisit when

- Akin runs with it on for two weeks and the reject rate on accepted items is near zero.
  Then consider making it the default, and a periodic "spot check five" review to keep
  the eval intake fed.
- The extraction golden set needs real cases and none are arriving: that is this setting
  working as designed. Turn it off for a week, or add a spot-check prompt.

## Interview angle

> **"Users want to skip reviewing AI output. Why not just mark it approved?"**

Because "approved" is a signal other systems consume. If your feedback loop, whether
evals, fine-tuning data or a trust score, reads approved outputs as ground truth, then
auto-approving turns model outputs into training labels. You are grading, or training,
the model on its own predictions. That is label leakage, and it hides regressions: the
gate stops catching drift because the answer key drifts with the model. So record the
truth, "kept without review", as its own state. Let the UX treat it as approved, and keep
it out of anything that needs a human judgement.
