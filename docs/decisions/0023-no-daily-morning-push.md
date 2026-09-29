# 0023 — No daily push for the morning view; push waits for V4's quality bar

**Status:** superseded by [0047](0047-a-morning-push-only-when-the-day-has-something.md)
**Date:** 2026-09-28

## Problem

The morning view now shows on Home (handoff item 2), but only when the app is opened.
The handoff asked whether a push notification is worth an Apple Push Notification service
(APNs) setup. The real question is narrower than "should memo have push": it is whether
*the morning view* should interrupt the user every day.

The brief answers most of it already, and this ADR exists to check that answer rather
than restate it: push is V4, with "a brutal quality bar (hard-capped, specific, one-tap
actionable, thumbs up/down feeding evals; nudge act-on rate ≥~30%)", and "one great
nudge/week > seven mediocre".

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Nothing new**: Today stays a section on Home | Zero cost; respects "ration notifications brutally". | Attachment depends on the habit of opening the app; a busy morning skips it. |
| **Daily local notification** (`expo-notifications`, scheduled on the device, no server) | No APNs key, no server work. | Content has to be generic ("your morning is ready") or stale (computed at last app open). A daily generic ping is the textbook mediocre nudge the brief rules out, and it trains the user to swipe notifications away before V4's good ones arrive. |
| **Daily remote push via Expo's push service** (the industry default for Expo apps) | Fresh content after the 04:00 build; the standard path. | APNs key via EAS, a `push_tokens` table with RLS, token lifecycle (receipts, `DeviceNotRegistered`), a delivery-hour cron, and a Settings toggle. Journal content on the lock screen, relayed through Expo and Apple, is a privacy cost even as counts. Still daily, so still seven a week. |
| **Direct APNs from the server** | No third-party relay. | Everything above plus JWT signing and a `.p8` key on the server. Only worth it if Expo's relay becomes the problem. |

## Decision

**No morning push. Today stays pull-only until V4, when push arrives as an event-driven
nudge that meets the brief's quality bar, not a daily digest.**

## What we're accepting

- **Some mornings the view goes unseen.** That's the cost of not interrupting. It is also
  a signal worth having: if Today is useful, opening the app becomes the habit.
- **When push does come, the plumbing starts from zero.** No token table, no APNs key.
  None of it is hard; it is a day of work, and doing it now would be building for a
  notification we have decided not to send.

## Revisit when

- Two weeks of daily use (the brief's ship test) show the Today section going unread on
  days when it had something due. Then a *conditional* push, sent only when there's an
  event today or a confirmed todo due today, is the first candidate, not a daily one.
- V4 starts. Build it once, with feedback (thumbs up/down) and the act-on-rate metric
  from day one.

## Interview angle

> **"Would you add a push notification for this?"**

Not by default. A notification is the most expensive thing an app can ask of a user:
their attention, uninvited. A daily digest ping is the easiest one to build and the one
that trains people to ignore you, so it spends the channel you'll want later for the
nudges that matter. I'd make the in-app view good first, measure whether people open it,
and when push comes, make it conditional (only when there's something worth interrupting
for) and instrumented (did they act on it?). The costs I'd name: some mornings the view
goes unseen, and the plumbing (APNs credentials, token lifecycle, a delivery cron) is
deferred, not avoided.

## Superseded (2026-09-29)

Akin asked for a daily morning push. [ADR 0047](0047-a-morning-push-only-when-the-day-has-something.md)
builds it the way this ADR's revisit section described: conditional (only a day with
an event or a todo due), specific, once a day at most, and measured from the first
send (opened, 👍/👎, act-on rate). What this ADR warned about still stands, and 0047's
revisit trigger is the check on it.
