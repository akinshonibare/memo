# 0057 — An "On this day" widget

**Status:** active
**Date:** 2026-09-30

## Problem

memo already looks back: Today shows a note from this day in another year (ADR 0040),
or an idea worth another look (ADR 0020). Both only appear when you open the app. Akin
picked, from five sketched widgets, the one that brings that look-back to the Home
Screen.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| **Reuse Today's look-back, pushed from the app when Today loads** | One definition of "on this day"; no new server work; works like the Today widget | Shows what the app last saw: stale if memo isn't opened for days |
| A widget timeline fetched by the extension itself | Fresh every morning without opening memo | The extension needs the session token, a network call and its own error states; a lot for a nicety |

## Decision

**`OnThisDayWidget` (small and medium), fed by the morning view whenever it loads: the
note from this day in another year, else the resurfaced idea, else a quiet line that
there's nothing yet. A tap opens that note (`memo://note/<id>`, a new link) or Ideas
(`memo://ideas`).** The note's own date is printed at the bottom, so an old snapshot
still reads true.

## What we're accepting

- **Staleness:** the label says "A year ago today", which can be yesterday's today if
  memo hasn't been opened. The printed date keeps it honest.
- **A native change:** a new widget kind is part of the build (build 18), not an
  over-the-air update.

## Revisit when

- The widget is often stale: let the extension refresh its own timeline each morning.

## Interview angle

> **"How does an iOS widget get its data?"**

Widgets run in a separate extension process with a tight budget and render from a
timeline of snapshots. The simple, robust pattern is for the app to write the snapshot
(shared container / App Group) whenever it has fresh data; the extension only renders.
Fetching from the network inside the extension is possible but means sharing auth and
handling failures in a place with almost no UI. Put a date on anything time-relative,
so a stale snapshot is still correct.
