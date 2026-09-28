# 0020 — The morning view pins the idea and reads todos and events live

**Status:** active
**Date:** 2026-09-28

## Problem

The morning view was built once at 04:00 local and stored whole; `GET /v1/morning`
returned the stored payload. Once the app showed it on Home, the snapshot went visibly
wrong within hours: a todo rejected at 09:00 stayed on Today, a todo added at lunch did not
appear until tomorrow, and a deleted capture left a row that opened a 404. Editing and
adding derivations from the app (handoff item 3) makes all of these routine rather than
occasional.

The payload holds two different kinds of data, and that is the problem:

- **The resurfaced idea** is a choice. Choosing it writes a `resurfacings` row (the 60-day
  cooldown), and it has to stay the same all day. Re-picking it on every read would change
  it under the user and burn the cooldown on ideas they never saw.
- **Todos and events** are a query over current state. Their right answer is whatever is
  true now.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Keep the snapshot** (status quo) | No change. The industry default for "digest" features (email digests are frozen at send time). | Wrong as soon as the user acts. Fine for an email, which nobody expects to update. Not fine for a screen inside the app where the user just made the change. |
| **Rebuild when stale**: rebuild if any derivation has `updated_at > built_at` | Snapshot semantics stay; a rebuild keeps the idea. | Misses deletes: a capture delete cascades, the rows are gone, and `max(updated_at)` goes *down*, not up. Fixing that needs a tombstone or a row count, which is a second staleness signal to get right. A GET that writes. |
| **Invalidate on write**: every derivation/capture write path deletes or rebuilds today's view | The view is only ever rebuilt when something changed. | Correctness depends on every current and future write path remembering to invalidate: discipline at call sites, which the quality bar rules out. The cache-invalidation bug waiting to happen. |
| **Pin the idea, read items live** | Correct by construction for every kind of change, deletes included, with no invalidation logic. GET stays a pure read. The pinned idea is re-checked, so one rejected or deleted since 04:00 drops out. | Two derivation queries per GET instead of one row read. The stored payload's todos and events become a record of what 04:00 saw rather than what the API returns. |

## Decision

**The stored view pins only the resurfaced idea; `GET /v1/morning` reads todos and events
live on every request, and drops the pinned idea if its derivation has since been rejected
or deleted.**

## What we're accepting

- **More work per read.** Every GET now runs the todo and event queries. They are per-user
  scans filtered in Python on `payload` (no index on `due` or `starts_at`), so the cost grows
  with the user's total todos and events, not with today's. At personal scale this is
  microseconds. At real scale it needs expression indexes on the JSONB dates, or promoting
  `due`/`starts_at` to columns.
- **The nightly job does less.** It now exists to pick the idea at 04:00 local and to
  pre-warm the row. That is still real work (the cooldown is dated by the morning it was
  shown), but "built nightly" no longer means "frozen nightly".
- **Stored todos and events diverge from the API.** The `morning_views.payload` todo and
  event lists are only accurate as of `built_at`. Anything that later reads the row
  directly, such as a push notification, is reading 04:00's view. That is the right thing
  for a 04:00 push, and the wrong thing for anything else.

## Revisit when

- A push notification ships and needs the day's items. Decide then whether it sends the
  04:00 snapshot (fine) or should also read live.
- The Today query shows up in request logs as slow, or a user's derivation count passes the
  low thousands. Index the JSONB dates or promote them to columns.
- The morning view gains something else with a side effect (a "done for today" state, a
  second resurfacing). That belongs with the idea in the pinned half.

## Interview angle

> **"You precompute a daily digest. The user changes something at noon. What do they see?"**

First separate what in the digest is a *decision* and what is a *query*. A decision with a
side effect, like "which old idea do we show today", has to be made once and pinned, or it
changes under the user and the side effect fires repeatedly. A query over current state,
like "what's due today", should never be cached in a place the user can see change, because
invalidation is where these features rot: every write path has to remember, and deletes are
the one everybody forgets, since a cascaded row leaves no `updated_at` behind. So I store
only the decision and compute the rest on read. The cost I'd name unprompted: reads get more
expensive, and the stored row stops being the API response, so anything reading it directly
is reading the 04:00 view.
