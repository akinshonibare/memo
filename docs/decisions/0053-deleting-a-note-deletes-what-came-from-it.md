# 0053 — Deleting a note deletes everything taken from it

**Status:** active
**Date:** 2026-09-29
**Supersedes:** [0022](0022-deleting-a-note-keeps-its-derivations.md) (its "keep the items" half)

## What changed

ADR 0022 kept a deleted note's todos, events and ideas, detached, because Akin had
asked that a confirmed todo not vanish with a rambling memo. Using memo, he changed his
mind: *deleting a note should delete its extractions and all.* The reasoning in 0022
wasn't wrong; the premise moved. Its own revisit trigger named this case: deleting a
note to get rid of what came from it.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Keep detaching (0022) | Confirmed work survives a tidy-up | Not what Akin wants; orphans with no source |
| Ask on each delete: "keep the items?" | Both answers available | A question every time, for an answer Akin has now given once for all |
| Delete in the route only | One line | Any other path (a raw delete, a future bulk delete) would still detach |
| **Cascade in the database** | Every path does the same; the rule can't be forgotten | Deleting is now bigger and irreversible for the items too |

## Decision

**`derivations.capture_id` is `ON DELETE CASCADE` (migration 0022).** Deleting a note
deletes its todos, events, ideas, mentions, prayers, passages, activities and
suggestions, whatever their status, and through their own cascades their chunks,
citations and resurfacings. The route only removes the recording's bytes and the row.
The app asks "Delete this note and everything memo took from it?".

What the note changed elsewhere stays changed: a prayer it answered stays answered,
and a person first met in it stays in People (without that mention). Items detached by
deletes before this (capture_id NULL) stay as they are; `capture_id` stays nullable for
them, and the tests keep creating such rows so every route still copes.

## What we're accepting

- **A confirmed todo goes with its note.** If it mattered on its own, it has to be
  added by hand or the note kept. Phone copies (Calendar, Reminders) go on the next sync.
- **Habit days counted only from that note go**, and milestones recompute.
- **No undo**, as before.

## Revisit when

- Deleting a note to clean up loses something Akin wanted: add "keep its items" to
  the delete dialog, not the old default.

## Interview angle

> **"Where do you enforce a delete rule: application code or the database?"**

In the database when the rule is about what a row *is* (an item is part of its note),
because the database is the only layer every path passes through: the API route, a
bulk job, an admin console, a cascade from deleting the user. Application code is for
rules about *requests* (who may delete, what to ask first). And when you reverse a
decision, write down which premise changed, so the next reversal is informed too.
