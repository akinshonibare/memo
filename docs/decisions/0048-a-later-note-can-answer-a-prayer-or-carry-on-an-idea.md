# 0048 — A later note can answer a prayer or carry on an idea: suggested, never applied

**Status:** active
**Date:** 2026-09-29

## Problem

memo keeps every note and what it found in it, but it never connects a new note to an
old item it changes:

- **Prayers.** "Pray about the Lagos job" becomes an open prayer in Faith. Three weeks
  later: "GOT THE OFFER!!" The prayer stays open. The faith reader deliberately ignores
  the second note ("news of a prayer already answered: that is gratitude, not a new
  request", ADR 0042), so nothing links it back. Answered prayers are the heart of the
  Faith space (they come back a year on), and memo only knows one was answered if Akin
  remembers to tap it.
- **Ideas.** "Resurface one old idea each morning" in March; "make it weekly, not daily"
  in September. Two unconnected rows; the thinking is scattered.

The brief names this reconciliation (V4) and calls it the hardest problem in the system:
entity resolution, semantic dedup, and a decision policy. This ADR takes the first slice
of it, with the safest policy.

## Options

**Finding the changes**

| Option | Buys | Costs |
| --- | --- | --- |
| Embedding similarity alone, with a threshold (what related notes do, ADR 0028) | Cheap, deterministic | Similar is not "answers": "praying for the job again" is near the prayer and answers nothing. Can't say *how* a note relates |
| A model over the note and *every* open prayer and idea | Accurate | A model call on every note, over a list that only grows |
| **Two steps, as in entity resolution: cheap candidates (blocking), then a model decides (matching)** | Accuracy where it matters, cost only on notes that could match | Two things to tune: the gates and the prompt |

**What happens when one is found**

| Option | Buys | Costs |
| --- | --- | --- |
| Apply above a confidence threshold | No taps | A wrong "answered" changes a prayer's story, silently; confidence is not calibrated |
| **Always ask, one tap** | Nothing changes without the author; every "no" is a label | A question to answer |
| Link silently, show as "related" | No taps | Doesn't close the prayer, which is the point |

## Decision

**A new optional pipeline stage, `reconcile`, after embedding. It finds candidates
cheaply, asks a model which ones the note really changes, and records each as a
suggestion the author answers with one tap.**

- **Candidates.** Open prayers, only for someone with Faith on and only when the note has
  an answer-ish word ("offer", "came through", "went well", "decided", "finally",
  "thank", ...), at most 20, newest first. Ideas: the 5 nearest the note, by the note's
  own embeddings from the embed stage (so no extra embedding call, which matters under
  Voyage's rate limit), under a cosine distance of 0.45. Items from the same note, or
  written after it, are never candidates.
- **The decision.** `reconcile-v1` (claude-opus-5, structured output) gets the note and
  the numbered candidates (P1, P2, I1...) and returns the changes: which one,
  `answers` (prayers) or `continues` (ideas), and **a quote from the note**. Validation
  checks the quote is really in the note (case and spacing aside), the label was
  offered, and the relation fits its kind; a failure goes back once for repair
  (ADR 0015). The quote is what the author reads before saying yes, so it must be their
  words.
- **The record.** A derivation of kind `update`, from the new note, with `target_id`
  pointing at the prayer or idea: a same-user foreign key with `ON DELETE CASCADE`,
  and a CHECK that only updates have one (migration 0020). Always `pending_review`,
  even with "Keep without asking" on (ADR 0030 is about new items, not changes to old
  ones).
- **Yes / no.** Faith shows "Answered?" and Ideas shows "Carries on an idea?", each with
  the quote (tapping it opens the note). A yes to "answers" marks the prayer answered on
  the day the note was written, with the quote as how, unless it was already marked by
  hand (that answer stands). A yes to "continues" changes nothing on the idea: the
  idea's thread is read from its confirmed updates and shown under it. A no is kept,
  and the same note never asks about the same item again.
- **Gated by an eval.** `evals/reconcile`: 26 cases (answers, near-misses like "still
  waiting" and "praying for it again", continuations, related-but-different ideas, and
  one note with both). Precision bar 0.90, recall 0.75. Recorded live and replayed in
  CI. It also checks the answer-word gate never skips a case that answers a prayer; on
  its first run it caught one ("house sale completed", now covered).

## What we're accepting

- **An answer phrased without any of the gate's words is never compared** ("Ada starts
  Monday!"). That costs a tap in Faith; comparing every note with every prayer costs a
  model call per note, forever. The eval's gate check is where new phrasings go.
- **0.45 is a guess** for which ideas count as near, inherited from related notes. Too
  loose costs model calls and "no" taps; too tight misses continuations.
- **About a cent per compared note.** Only notes with candidates pay: someone with no
  open prayers and no near ideas pays nothing.
- **The eval is a starter set Claude wrote, and 1.00 on it says the prompt is sane, not
  that it's right on Akin's notes.** Real cases come from his yes and no taps: each is a
  label.
- **Duplicate ideas stay separate.** If the new note also produced a fresh idea that is
  the same idea, both cards remain; the new one is linked by the thread, not merged.
  Merging is the next slice.

## Revisit when

- **"No" outnumbers "yes"** over a few weeks: tighten the prompt, or raise the bar with
  a confidence threshold below which nothing is asked.
- **Duplicate ideas pile up:** fold a new idea into the one it continues, on yes.
- **Other kinds want it:** todos ("done it" in a later note), events ("moved to
  Friday"). Same stage, new candidates, new relations.
- **Akin says the questions are a chore:** that is the signal for auto-apply above a
  measured precision, with undo, not before.

## Interview angle

> **"How would you detect that a new record updates an old one?"**

Treat it as entity resolution: blocking, then matching. Blocking finds a small candidate
set cheaply (keyword gates, nearest neighbours by embedding) so the expensive step runs
rarely; matching decides with something that understands the relation, here a model,
because "similar" and "answers" are different questions. Make the matcher show its
evidence (a quote, checked against the source), measure precision on the error that
hurts (a false "answered"), and keep a human in the loop until the numbers earn
automation. Every yes and no is a free label for the next version.
