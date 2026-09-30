# 0055 — Deleting a person forgets them and keeps the notes

**Status:** active
**Date:** 2026-09-30

## Problem

memo meets people from notes (ADR 0029) and you could rename, merge or correct them,
but never delete one. Akin deleted every note about someone and the person stayed:
since ADR 0053 a note's mentions go with it, but the person row doesn't, and some
"people" were never people at all (a café called Pilot).

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| **Delete the person and their mentions; keep notes and prayers** | Forgotten means forgotten; nothing reappears as "who is this?" | A later note naming them makes a new person |
| Delete the person only (mentions become unlinked) | Least code | Every mention turns into a "which one?" question, the opposite of what was asked |
| Delete the person and every note about them | Thorough | Destroys writing to remove a name; far too much for one tap |
| Remove a person automatically when their last note goes | No tap needed | Surprising: a person you gave a note, family flag or rhythm could vanish |

## Decision

**`DELETE /v1/people/{id}`**, under the people lock (ADR 0029): the person goes, with
every mention of them (who notes were about, contacts you logged), and through the
cascade what Ask indexed of those mentions. Notes are untouched. A prayer for them
stays, its link to the person set null by the foreign key. The person's page has
"Delete person" in its menu, confirmed first.

## What we're accepting

- **A later note that names them meets them again** as someone new, without the old
  aliases, note or rhythm. That's what "forget" means; merge is the tool for "these
  are the same person".
- **"Time with family" loses that person's days** if they were family.
- **No undo.**

## Revisit when

- Deleting someone keeps being followed by them coming back from new notes: add
  "never suggest this name" (a block list the resolver checks).
- People with no notes left pile up: offer to clear them in one go.

## Interview angle

> **"How do you delete an entity that other records point to?"**

Decide per reference what the delete means, and let the database carry it: here
mentions *are* the person in context, so they go; prayers are the user's writing, so
they stay with the link nulled. Take the same lock as the other writes to that entity
(resolution and merge), so a note being processed can't attach to a row mid-delete.
