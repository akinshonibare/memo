# 0040 — On this day: one note from this date in another year, read live

**Status:** active
**Date:** 2026-09-29

## Problem

A capture-everything app accumulates a past that nobody looks at. The morning view
already resurfaces one old *idea* a day (issue #38). A note from this date a year ago is
a different kind of memory, and the kind people come back to an app for.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| A feed of everything from this date | Complete | A chore by the third item. The brief rations push-side retrieval on purpose: one idea, never five. |
| **One note, chosen by a fixed order: whole years back (furthest first), then six months, then one month; on a busy day, the longest note** | A memory, not a list; deterministic, so it's the same all day | Most days have no match early on. Six-month and one-month look-backs fill the gap, but less poignantly. |
| Pin it at the 04:00 build, like the idea | Stable by construction | A note deleted at 09:00 would stay; pinning only earns its keep when the choice has a side effect (the idea records a resurfacing). This one doesn't. |

## Decision

**`MorningViewOut.on_this_day`: at most one note, computed live on every read,** like
todos and events (ADR 0020).

- **Only finished notes with words** qualify (`status = ready`, non-empty text).
- **The note's local date is compared in the user's timezone.** Each candidate day is
  turned into the UTC instants it spans, local midnight to local midnight, in Python;
  the query is a handful of plain `created_at` ranges OR'd together. A note written at
  00:30 in London a year ago counts as that date, not the previous UTC day, and one at
  00:30 the next day doesn't. Both edges are tested. (The first version converted every
  row in SQL with `timezone(zone, created_at)::date`; review caught that no index can
  serve that.)
- **Days that don't exist are skipped, not moved.** On 29 February, a year back means the
  last 29 February; 31 March has no six-month match (there's no 31 September).
- **Tenant isolation:** an explicit `user_id` filter under row-level security. Tested with
  a second user.
- **In the app:** a card in Today, "A year ago today" / "2 years ago today" / "Six months
  ago today", which opens the note. An older server sends no field, and the card simply
  doesn't appear.

## What we're accepting

- **One more query on every `GET /v1/morning`.** Up to twelve short ranges on
  `(user_id, created_at)`, which `ix_captures_user_created` serves.
- **Nothing for a new user** until a month has passed.
- **The longest note wins** a busy day. "Longest" stands in for "most substance", which
  won't always be right.

## Revisit when

- A user moves timezone often and "on this day" feels wrong: store the local date a
  note was written in, rather than reading it through today's timezone.
- People want to skip a memory ("not this one"): record it like a resurfacing.

## Interview angle

> **"How would you build an 'On this day' feature?"**

Pick a rationing rule before a query: one item, chosen deterministically, so it doesn't
change between refreshes. Do the date comparison in the user's timezone, not UTC, or late
evening notes land on the wrong day - and do it by converting the *day* to a UTC range,
not every row to a local date, so the timestamp index still works. Decide explicitly what happens to dates that don't
exist, such as 29 February. Compute it on read unless choosing it has a side effect; a
snapshot would keep showing something the user just deleted. And ship it so that a missing
field is a missing card, never a crash.
