# 0062 — Lately: a line on each person, written ahead from their notes

**Status:** active
**Date:** 2026-09-30
**Amends:** [0029](0029-people-last-contact-and-reach-out.md) (what a person's page shows first)

## Problem

Akin: *every person should have a short highlight on the most recent things, so I can
see at a glance when I go to their page.* A person's page had the notes that mention
them, newest first, and the last time you were in touch. To know what's going on with
Kemi you read four notes.

(Called **Lately**, not "highlight": Highlights are what memo keeps from notes, ADR 0060.)

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| The last three things the notes said about them, with the day (no model) | Free, instant, can't be wrong | A list, not a glance; repeats the timeline below it |
| **A line or two written by a model, ahead of time, rewritten when their notes change** | Reads like a friend catching you up; the page opens instantly | A model call per change, a new prompt that can say something untrue, and staleness to track |
| The same line written when the page opens | Always fresh | Seconds of waiting on every visit, and a call per visit instead of per change |

Sketched both A and B for Akin; he chose the written line.

## Decision

**`people.lately` is written by `lately-v1` from a person's six newest live mentions
(date, whether you were in touch, what the note says about them, the note's opening
words), in the person's tone with no em dashes (ADR 0061), at most 220 characters,
empty when the notes only name them in passing.** It uses their name and never a
pronoun the notes don't use.

**A worker sweep (`refresh_lately_task`, four times an hour) rewrites whoever is out
of date**, most recently mentioned first, twenty at a time. The row records what the
line was written from (`lately_at`, `lately_count` of live mentions, `lately_tone`,
`lately_version`; migration 0028), and a person is out of date when a mention was
added or changed since, the count differs (a note deleted, a mention rejected or
moved by a merge), the tone changed, or they were renamed. With no mentions left the
line is cleared without a model call. A failure that won't fix itself is stamped, so
it isn't paid for again until their notes change; a transient one stops the batch.

`evals/lately` (10 cases, a CI gate) holds the lines to their notes: no invented
names, no guessed pronouns, no fact a newer note replaced, nothing over length, and
empty for a name in a list.

## What we're accepting

- **Up to fifteen minutes behind.** A note about Kemi shows on her page at once; the
  line catches up on the next sweep.
- **A model's summary of a relationship can be wrong.** The notes are right below it,
  and the eval's hardest bars are on inventing, not on missing.
- **Cost per change, not per view:** about $0.01 a rewrite on `claude-opus-5`. A tone
  change rewrites every person with notes once.
- **Six notes is a window.** Something big from last year falls out of the line once
  six newer notes mention them, even if they're small.

## Revisit when

- People with hundreds of notes feel misrepresented by their six newest: weigh by
  significance, not recency.
- The sweep's query shows up in slow-query logs: index `derivations (person_id,
  updated_at)` for mentions, or enqueue a rewrite from the people stage instead.

## Interview angle

> **"You want an LLM summary on a page. When do you generate it?"**

Ahead of time, keyed to what it summarises. Store the summary with a description of
its inputs (here: how many notes, the newest one's timestamp, the tone) and let a sweep
regenerate whatever no longer matches. Reads stay fast and free, cost scales with
changes rather than views, and deletions invalidate it too, because a count catches
what a timestamp can't.

> **"How do you evaluate a generated summary?"**

Test the failures that matter with cheap deterministic checks before reaching for an
LLM judge: names that aren't in the input, pronouns the input never used, facts a newer
input replaced, length. Bar them at 1.00. Score "did it pick the right thing" loosely;
that's where reasonable summaries differ.
