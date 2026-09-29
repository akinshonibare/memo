# 0034 — memo on the phone: Calendar and Reminders are Settings switches that sync, not per-item buttons

**Status:** active
**Date:** 2026-09-29

## Problem

Akin asked to connect memo to the phone's calendar. The first build offered "Add to
Calendar" and "Add to Reminders" on each item (a swipe, a button). His correction: *adding
to calendar, reminders, events should be a settings trigger, not per event or item.* One
switch, and memo keeps the phone up to date by itself.

That is a synchronisation problem, which is where the bugs live:

- **Duplicates.** A pass that fails halfway, then runs again.
- **Deletions.** memo deletes an item, or you delete memo's copy in Calendar.
- **Conflicts.** You tick a todo in Reminders while memo still has it open.
- **Partial knowledge.** memo lists only the newest items; is an older one missing, or
  just not fetched?

The other direction, showing your own calendar inside memo, was built and then taken
out at Akin's request: *the events I see in the app should only be the app's.* memo writes
to the phone; it never reads your events.

## Options

**Where memo's copies live**

| Option | Buys | Costs |
| --- | --- | --- |
| Your default calendar or list | Nothing new appears in Calendar | memo would be updating and deleting among your own events: one bug and it removes a meeting you made |
| **A calendar and a Reminders list called "memo", created and owned by memo** | memo can only ever touch its own container. Turning it off is deleting that container, which is clean and complete. It can be hidden or recoloured in Calendar. | One more calendar and list on the phone. Full calendar access (write-only can add but never find its events again to update them). |

**How changes flow**

| Option | Buys | Costs |
| --- | --- | --- |
| Push on every change (a queue of adds, updates and deletes) | Immediate | A lost message means drift forever; ordering bugs; retries create duplicates |
| **Reconcile: each pass compares memo, the phone and the last pass's record, and changes only the difference** | Idempotent: run it twice and the second pass does nothing. Self-healing after a failure or an offline spell. | A pass fetches memo's items and lists the container every time (debounced; up to 1,000 items) |
| Full two-way sync of all fields | Edit anywhere | A merge story for titles, dates and deletions on both sides: out of all proportion for a personal app |

## Decision

**Three switches in Settings → On this phone: *Put events in Calendar*, *Put todos in
Reminders*, *Remind me before events* (ADR 0036). memo writes to the phone and never reads
your own events: the Planner and Today show memo's events only.**

- **One reconciler, `src/sync.ts`.** Its decisions live in pure functions in
  `src/syncPlan.ts`, checked by a scenario script: create, update, delete, detach, a
  cut-short list, and every completion case. It runs on sign-in, after any change memo
  makes, when a switch flips, and on every return to the foreground, debounced. Passes,
  switch flips and sign-out run one at a time, in order, through a single promise chain.
- **Events: one way.** memo is the source of truth. An event memo adds, moves or rejects
  is added, moved or removed in the "memo" calendar, within a window from 30 days back to
  a year ahead. An edit you make to one of those copies in Calendar is overwritten on the
  next pass.
- **Todos: content one way, completion both ways.** Tick a todo in Reminders and memo
  ticks it (and the other way round). A **three-way merge** against the state recorded at
  the last pass says which side moved. If both moved and disagree, *done* wins: undoing a
  tick is rarer than making one, and a wrongly-open todo is easy to see.
- **You delete a copy yourself → memo leaves it deleted** (the mapping is marked
  `detached`). It never fights you by putting it back.
- **The list can be cut short.** memo fetches at most 1,000 items, newest first, and says
  whether it stopped early. An item absent from a cut-short list is only treated as
  deleted if it's newer than the oldest one fetched. Otherwise its copy is left alone.
- **Every copy is tagged with its memo item,** `memo://item/<id>` in its URL field. Each
  pass reads the tags back first (`heal`): a copy memo has no record of is mapped to its
  item instead of being added again, and a second copy of the same item is deleted. So a
  pass killed halfway, a lost `phone-sync.json` or a reinstall never duplicates anything.
  Progress is also saved as it's made. The record lives on the phone, never the server.
- **memo only touches a calendar or list whose id it recorded when it made it.** The
  ids are kept in the keychain, so they survive a reinstall. A calendar of yours that
  happens to be called "memo" is never adopted and never deleted. A container you delete
  yourself is recreated (the switch is still on), and the tags rebuild the mapping.
- **Fields memo clears are cleared on the phone:** a location, notes or due date removed
  in memo is removed from the copy, not left behind.
- **An event to remove from outside the window** is looked up on the day it was put
  (recorded when it was added or moved), so a rejected event two years out still goes.
- **Turning a switch off deletes memo's calendar or list,** and everything in it. If the
  Reminders list itself can't be deleted, memo deletes its todos from it and says so.
- **Signing out, or deleting the account, takes memo's copies off the phone:** its
  calendar, its list and its pending notifications belong to the account. The switches
  stay on, so signing back in puts everything back.
- **memo only ever lists its own calendar and list.** It needs full access to find its
  own copies again, but it never lists, shows or sends events from your other calendars.

## What we're accepting

- **Full calendar access** for the event sync. Write-only access can create events but
  can't list them, so it can never update or remove what it added.
- **Edits in Calendar don't come back** to memo, and are overwritten.
- **Completion from Reminders reaches memo on the next foreground,** not instantly. There
  is no background EventKit observer.
- **A completion pulled from Reminders confirms an unreviewed todo,** exactly as ticking it
  in memo does (the existing PATCH rule). It's a real signal: you did it.
- **Your other plans aren't in memo.** The Planner shows only what memo knows about, so it
  isn't a full picture of your day. That's the point: memo is where your notes live, and
  Calendar stays the place for everything else.

## Revisit when

- Edits made in Calendar need to come back: that means field-level two-way sync, and a
  proper conflict story.
- More than a few hundred future events: page the reconcile by date, not by recency.
- Another device: each phone reconciles its own copy, and `detached` is per device. That
  is fine until it isn't.

## Interview angle

> **"How would you sync your app's data into the user's calendar?"**

Don't write a queue of changes; write a reconciler. Each pass computes the difference
between the source of truth, the destination, and what the last pass recorded, then
applies only that difference. It's idempotent and heals itself after a failure. Tag each
copy with the id of what it came from, so losing your own bookkeeping degrades to a
re-read, not duplicates. Keep your copies in a container you own, identified by the id you
recorded, never by a name the user could also use, so a bug can only ever touch your own
data and turning the feature off is one delete. Decide the direction of each field explicitly: content one way,
completion both ways with a three-way merge against the last known state. And be careful
with "absent means deleted" when your read was paginated.
