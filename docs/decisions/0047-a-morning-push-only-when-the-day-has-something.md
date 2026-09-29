# 0047 — A morning push, only when the day has something in it

**Status:** active
**Date:** 2026-09-29
**Supersedes:** [0023](0023-no-daily-morning-push.md)

## Problem

ADR 0023 said no to a daily morning push: a notification is the most expensive thing an
app can ask for, and a daily digest trains people to swipe notifications away. Akin
reversed that on 2026-09-29 ("we can do daily morning push"): he wants Today to come to
him instead of waiting to be opened.

The reversal is the owner's call. The job here is to keep what 0023 got right while
doing it: 0023's own revisit section already named the shape (a push that is
*conditional*, sent only when there's an event or a todo due today, with 👍/👎 and an
act-on rate from the first day). The questions left are how it's delivered, how it
stays once a day, and what it's allowed to say.

## Options

**Delivery**

| Option | Buys | Costs |
| --- | --- | --- |
| **Remote push through Expo's push service** (the default for an Expo app) | Written after the 04:00 build, so it's today's content; Expo holds the APNs key | A relay (Expo, then Apple) sees the text; a token table and its lifecycle |
| Local notification scheduled on the phone | No server, no relay | Content computed at the last app open, so stale by morning; nothing to send when the phone hasn't opened memo |
| Direct APNs from the server | No relay | A `.p8` key on the server and JWT signing, for no benefit at this scale |

**Once a day**

| Option | Buys | Costs |
| --- | --- | --- |
| **Claim a row (unique per user, kind, local date) before sending** | At most once: a crash after the claim loses that morning's push | A missed push, rarely |
| Send, then record | At least once | A crash between the two sends it twice; a duplicate costs more trust than a miss |

**Where a push can go**

| Option | Buys | Costs |
| --- | --- | --- |
| **Tokens tied to the session that registered them** | Sign-out and "sign out everywhere" (a lost phone) stop pushes at once, with no help from the phone | A new sign-in must register again (the app does, on launch) |
| Tokens tied to the user only | Simpler | A lost phone keeps showing your day on its lock screen until the app says otherwise, which it can't |

## Decision

**A remote push through Expo, at a time the user sets, claimed once a day, sent only to
signed-in phones, and only when the day has something in it.**

- **What it says:** today's events by time, then todos due today: "Today: 10am Dentist"
  / "2:30pm Call with Tunde · Renew passport". One item: its location, or "Nothing else
  on." More than four: "· 2 more". **A todo from an earlier day is never pushed**: it
  stays on Today in the app. Pushing the same slipped todo every morning is the guilt
  the brief rules out.
- **When:** `users.morning_push_at`, a local time (default 07:30, from Settings). A cron
  every five minutes sends to everyone whose time has come and hasn't passed by more
  than two hours: a 07:30 summary delivered at lunchtime is a different, worse message.
- **Nothing on:** the day is recorded as `skipped` ("nothing today", or "no phone"), so
  "why didn't I get one?" has an answer and the act-on rate has an honest denominator.
- **Feedback from the first push:** a tap records `opened`; the notification's 👍 / 👎
  buttons record a vote (and count as opened). `GET /v1/pushes` gives the month's sent,
  opened, act-on rate and votes. The brief's health metric is an act-on rate of 30% or
  better.
- **Token lifecycle:** Expo answers with a ticket per phone; a receipt fifteen minutes
  later says whether Apple took it. `DeviceNotRegistered` from either disables the
  token. A phone re-registers on every launch while the push is on, which re-enables it.
- **The app side is JavaScript only.** Build 17 already carries the push entitlement
  (the `expo-notifications` plugin always adds it), and its native fingerprint
  (`79d9bbf7`) is unchanged, so this ships as an over-the-air update.

## What we're accepting

- **Some mornings a push is lost** (a crash between claim and send, or Expo down at
  07:30). Chosen over ever sending two.
- **Your day passes through Expo and Apple.** The lock screen shows it unless the
  iPhone's own "Show Previews: When Unlocked" is set. Titles and bodies are stored on
  the push row, and deleted with everything else.
- **👍 and 👎 open the app.** iOS only hands an action to JavaScript without opening the
  app if memo happens to be running, so a vote from a background button would usually
  be lost. One extra step, in exchange for votes that arrive.
- **One more thing Akin must do by hand:** the APNs key goes onto Expo with `eas
  credentials` (an Apple login). Until then Expo refuses the sends, and each push row
  says why.

## Revisit when

- **Act-on rate under 30% over two weeks**, or more 👎 than 👍: the content is wrong or
  the time is. Try a later time, or send only when there's an event (not just todos).
- **Event-driven nudges** (V4) arrive: they reuse the tokens, the ledger and the
  feedback, and get their own kind in `pushes`.
- **More than a handful of users**: the five-minute sweep reads every user with a time
  set; batch it by local time.

## Interview angle

> **"How would you build a daily notification that people don't learn to ignore?"**

Make it conditional, specific and measured. Send only when there's something to say, say
the actual thing ("10am Dentist", not "your day is ready"), and never repeat what the
user already let slip. Instrument it from the first send, opened and a thumbs up or down,
and set the metric that decides whether it survives (act-on rate). On delivery, claim
before you send: for notifications, at most once is right, because a duplicate costs more
trust than a miss. And tie device tokens to the session, so signing out, or revoking a
lost phone, stops the pushes without depending on the phone.
