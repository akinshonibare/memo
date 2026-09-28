# 0022 — Deleting a note removes the note and keeps what the user got out of it

**Status:** active
**Date:** 2026-09-28
**Closes:** [#55](https://github.com/akinshonibare/memo/issues/55), [#56](https://github.com/akinshonibare/memo/issues/56)

## Problem

Two problems, one question: what does deleting a note mean?

- **#56, the product:** deleting a voice memo also deleted the todo that came out of it.
  A confirmed "renew the car insurance by 5 October" shouldn't vanish because the
  rambling memo that produced it was tidied away. Akin asked for this directly.
- **#55, a live bug:** deleting any note an Ask answer had cited failed with a
  foreign-key violation. Deletion cascaded to chunks, and `message_citations.chunk_id`
  had no `ON DELETE` action, so the delete returned a 500.

The old answer was "hard delete, everything cascades", chosen because an orphaned text
chunk would keep answering questions about a note that was gone. That concern is right
and stays. What was wrong was treating derivations as the note's *text*. They are the
user's *output*: reviewed, confirmed, sometimes written by hand.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Cascade everything** (status quo, citations fixed with a cascade) | Simplest; "delete" means one thing. | Loses confirmed work the user asked to keep. Fails #56 by design. |
| **Soft-delete the capture** (`deleted_at`, filter everywhere) | Keeps provenance; undo is possible. | The note's text stays in the database and in the index. Every retrieval, list and export path must filter it forever, and one that forgets leaks a deleted note into an answer. This is the reason the original ADR rejected soft delete, and it still holds. |
| **Detach the derivations**: `capture_id` becomes nullable; text and text chunks are hard-deleted; non-rejected derivations and their chunks stay with `capture_id = NULL` | The note is really gone; the user's items stay and stay searchable. The invariant "no text chunk without a note" is a `CHECK`, so it's enforced by the database, not by discipline. | Provenance is lost: a kept todo can't show where it came from. Nullable `capture_id` ripples through every consumer (API schemas, Today, citations, retrieval dedupe, export, app). |
| **Ask on each delete**: "keep the items?" | User decides per note. | A question on every delete for an answer that is almost always "keep". It's the Detach option plus a dialog, so do that first and add the dialog if anyone ever wants the other answer. |

For citations, the sub-question is what happens to an answer that cited a deleted
note's chunk:

| Option | Cost |
| --- | --- |
| Cascade the citation | The answer's prose stays, and may paraphrase the deleted note. |
| Delete the whole answer message | Rewrites conversation history the user didn't ask to touch. |
| `SET NULL` plus a "source deleted" marker | Not possible: `chunk_id` is part of the citation's primary key. It would need a schema redesign for a marker. |

## Decision

**Deleting a note hard-deletes its text, recording, text chunks and rejected derivations.
Everything else extracted from it is kept, detached (`capture_id = NULL`), and stays
visible in Today, Ask, the Items screen and the export. Citations cascade with their chunk.**

Mechanics (migration 0008):

- `derivations.capture_id` and `chunks.capture_id` become nullable. The composite foreign
  keys use `ON DELETE SET NULL (capture_id)`, the Postgres 15+ column-list form, because a
  plain `SET NULL` on `(capture_id, user_id)` would also null `user_id` and drop the row out
  of its tenant.
- `CHECK (capture_id IS NOT NULL OR derivation_id IS NOT NULL)` on chunks. The route deletes
  text chunks first; if any path ever forgets, the `SET NULL` violates the check and the
  delete fails loudly rather than leaving the note's text searchable.
- `message_citations → chunks` is `ON DELETE CASCADE`.
- Delete-everything deletes chunks and derivations explicitly, because detaching is right
  for one note and wrong for "everything".

## What we're accepting

- **Answers can still paraphrase a deleted note.** The citation goes; the prose doesn't.
  "Delete means gone" is true for the note and not for conversations about it. Deleting the
  conversation is a separate act.
- **Kept items lose their provenance.** No "from your note on 27 September" once it's
  deleted. The derivation's `created_at` is all that's left.
- **Pending items survive too.** An unreviewed extraction from a deleted note can still be
  reviewed, now without its source text to check against. That was the simpler rule, and it
  matches "keep what was extracted". It's revisitable if it turns out to be noise.
- **SQLAlchemy can't express the constraint**, so the model's foreign key carries a comment
  instead, and migrations are the only source of DDL (they already were).

## Revisit when

- Someone deletes a note *to* get rid of its items, which would show up as immediate
  follow-up deletes of the detached items. Add the per-delete "keep the items?" choice.
- Answers quoting deleted notes become a real privacy concern. Then deleting a note
  should also redact or remove the answers that cited it.
- Provenance matters: keep a short, user-visible "from a note on <date>" string on the
  derivation at delete time.

## Interview angle

> **"A user deletes a record. What else goes with it?"**

Split what's derived from the record into things that *are* the record and things the
user *made from* it. The note's text, audio and embedded chunks are the note, and they
must really go: an embedding left behind keeps answering questions about something the
user deleted, which is the worst kind of leak because it's invisible. The todos they
confirmed are their work product, and deleting those with the note is data loss dressed as
tidiness. So the foreign key detaches instead of cascading, and the invariant "no text
chunk without its note" is a CHECK constraint, not a line of code someone has to remember.
The detail that catches people: on a composite tenant-scoped foreign key, `SET NULL` nulls
*both* columns, including the tenant id, so you need the column-list form. The cost I'd
name unprompted: an answer that already paraphrased the note keeps its prose.
