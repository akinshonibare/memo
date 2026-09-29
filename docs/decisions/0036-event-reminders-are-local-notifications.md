# 0036 — Event reminders: local notifications, scheduled by reconciling, off by default

**Status:** active
**Date:** 2026-09-29

## Problem

memo pulls events out of notes ("dinner with Sam Friday at 7"), but nothing reminds you
when Friday at 7 comes. ADR 0023 decided against a *daily* push. That was about a digest,
a habit-forming nudge. This is different: one reminder per event you would otherwise
miss.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Server push (APNs) | Works even if the app hasn't opened since the event was created | A push credential, device tokens, a scheduler, a delivery pipeline: a whole system for a personal app |
| Rely on "Put events in Calendar" (ADR 0034) and Calendar's alerts | Nothing new | Only if you sync to Calendar, and alerts depend on your calendar's defaults |
| **Local notifications (`expo-notifications`), scheduled on the phone** | No server, no token; exact times; works offline | Only as fresh as the last time memo was opened; iOS keeps at most 64 pending |

## Decision

**Settings → On this phone → Remind me before events, off by default, with a lead time of
5, 15, 30 or 60 minutes.**

- **Scheduling is a reconcile,** part of the phone sync (ADR 0034). Each pass works out
  the notifications there should be, cancels the ones that shouldn't exist (an event moved,
  was rejected, or passed) and adds the missing ones.
- **The start time is part of a notification's identity,** so a moved event replaces its
  reminder rather than adding a second one.
- **Limits:** the soonest 50 events within 30 days, under iOS's cap of 64.
- **Tapping one opens the note the event came from,** or the Planner for an event added
  there. It waits for sign-in and unlock like any link.

## What we're accepting

- **An event created while memo is closed is only scheduled on the next open.** Most
  events come from notes taken in the app, so the app was open.
- **Installing `expo-notifications` adds the push entitlement** (`aps-environment`) even
  though memo sends no pushes. EAS syncs the App ID's capability.
- **The notification shows the event's title** on the Lock Screen. It's your own phone,
  and it's opt-in.

## Revisit when

- Events start arriving from somewhere the app doesn't see open, such as a shared
  calendar or email: then server push earns its place.
- Reminders for todos with a time.

## Interview angle

> **"How would you add reminders without building push infrastructure?"**

Schedule local notifications on the device, and treat the pending set as state to
reconcile rather than a list to append to. Compute the notifications that should exist
from the source of truth, diff against what's scheduled, and cancel or add. Encode
anything that changes the meaning, such as the event's start time, into the notification's
identifier, so an edit replaces instead of duplicating. Know the platform's limit (64 on
iOS) and schedule a rolling window. Push infrastructure is for when the device isn't the
one that knows.
