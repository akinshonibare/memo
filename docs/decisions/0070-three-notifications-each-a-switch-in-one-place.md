# 0070 — Three notifications, each a switch, all in one place

**Status:** active
**Date:** 2026-10-02

## Problem

Akin (2026-10-01): *think of where app notifications make sense, give users the
options for them; we don't want too much.*

memo could already notify twice: the morning summary (ADR 0047, a remote push) and a
reminder before an event (ADR 0036, a local notification). They lived in two Settings
groups ("Morning", and "On this phone" among the calendar switches), the `pushes`
ledger could only ever hold one kind, and nothing in the app said how many
notifications memo might send. The brief sets the bar: *ration notifications
brutally; one great nudge a week beats seven mediocre ones*, no guilt mechanics, and
V4's push rule: hard-capped, specific, one-tap actionable, thumbs up or down from
the first send.

So two questions: which moments earn a notification at all, and how a person sees
and controls the whole set.

## Options

**Which moments.** Every candidate was held to the same four tests: is it specific
(says the thing, not "you have updates"), is it conditional (sent only when something
is true), is it one tap from the thing it names, and is it free of guilt (never a
count against you, never a cadence you fell behind).

| Moment | Buys | Costs | Verdict |
| --- | --- | --- | --- |
| Morning summary (0047) | The day comes to you | A daily interruption, on days with something on | **Kept** |
| Before an event (0036) | Not missing it | One per event, local, no server | **Kept** |
| **Sunday: your week** | One look back a week, the brief's own cadence; the weekly review exists and goes unopened | A Sunday-evening interruption; numbers can read as a score | **Added**, counts only, nothing against you |
| What memo found in a note | See a Siri or Action Button note's items without opening memo | At twenty notes a day it is the noisiest thing memo could do; a saved note already opens in the app | No |
| A note memo couldn't read | Rare and actionable | Already visible in the app with Retry; needs a capture that went unseen | Not now (see revisit) |
| Reach out to someone | The person comes to mind | Cadence-based, the nudge the brief moved away from; it stays on Today where "Not now" is one tap | No |
| A habit gone quiet | A gentle prompt | Guilt by push, however gentle the words | No |
| A milestone (first 10k) | Delight, rare and earned (0041) | A push of its own for something the morning summary could carry | Not now (see revisit) |
| On this day | Attachment, the brief's resurfacing | A second daily push, or a near-daily morning one | No; it is on Today and in a widget (0057) |
| Answered a prayer? Carries on an idea? | A yes while it's fresh | A review question by push is a nag | No |
| "How was your day?" | A capture habit | The generic daily ping 0023 refused; it trains the swipe-away | No |
| Daily AI limit reached (0063) | Knowing why a note waits | You are in the app when it happens, and it says so there | No |

**Where the switches live**

| Option | Buys | Costs |
| --- | --- | --- |
| By feature, where each already was (status quo) | Each next to what it's about | No one place says what memo may send; the promise "not too much" is invisible |
| **One Notifications group: every kind, a switch each, off by default, a footer that says these are the only ones** | The whole set in one glance; the promise is checkable | "Before events" moves out of "On this phone", where a current user looks for it |
| One master switch | Simplest | Loses the per-kind choice; someone who wants the week but not the mornings can't say so |

**What the week's push says**

| Option | Buys | Costs |
| --- | --- | --- |
| **Counts from the review's own queries** ("Your week: 14 notes" / "6 todos done · 3 highlights") | No model, no cost, nothing to be wrong; matches what the review shows when tapped | Numbers, not a sentence |
| A written line ("A week of planning the move…") | Reads as memo | A model call per user per week with no eval to check it; the review router made the same call and deferred it |
| The week's best highlight by title | Specific | Picks a winner; a push that chooses for you |

## Decision

**Three notifications, each off until turned on, each a switch in one Settings group
whose footer says these are the only notifications memo sends.** The third is new: on
Sunday at a time you choose (5pm to 9pm, 6pm by default), the week in numbers, only
for a week with something in it, a tap opening the weekly review.

- **Server:** `users.weekly_push_at` (migration 0031), `pushes.kind` may be `week`,
  and the same ledger carries it: claimed before sending, unique per user, kind and
  local date, so one per Sunday at most; sent only to signed-in phones; `opened`,
  👍 and 👎 recorded like the morning's. `services/push.py` composes it from the
  review's queries (`week_facts`): notes captured, todos done, new highlights, Monday
  to Sunday in the user's zone. A quiet week is `skipped` ("nothing this week").
  A five-minute sweep (`send_weekly_pushes_task`) sends it in the same two-hour
  window the morning gets.
- **App:** a category per kind of push, so a tap opens Today or the review;
  `src/push.ts` is one module for both. Settings → Notifications: Morning summary,
  Your week, Before events, each with its time. The calendar and Reminders switches
  stay under "On this phone": they are not notifications.
- **The cap is structural**, not a counter: one morning and one week row a day by the
  unique claim, the week only on Sundays, event reminders bounded by events (at most
  fifty scheduled, within thirty days). Nothing else in memo can send a notification.

## What we're accepting

- **A count is a number about you.** "Your week: 1 note" will read as a reproach to
  someone who had a slow week, even though nothing is compared or carried over and
  open todos are never counted. The quiet week sends nothing; the slow one sends a
  small number. If 👎 says so, the fix is a sentence (which needs an eval first) or
  fewer numbers.
- **Sundays can have two pushes**, morning and evening, ten hours apart and about
  different things. Still two.
- **The week's push arrives in a two-hour window or not at all**, like the morning's.
  A worker down at 6pm on Sunday loses that week's push; a late one isn't sent.
- **"Before events" moved.** Anyone who learned it was under "On this phone" looks
  again once.
- **Nothing is said in words**, only in numbers, until a written weekly line has an
  eval behind it.

## Revisit when

- **More 👎 than 👍 on the week's push over a month, or its act-on rate under 30%:**
  try a different hour, or say one thing instead of three numbers, or drop it.
- **A written weekly line gets an eval:** replace the counts with a sentence.
- **Captures from outside the app (Siri, the Action Button, the share sheet) become a
  real share of notes** and a failed one goes unseen for a day: the "memo couldn't
  read your note" push, rare and one-tap to Retry.
- **A milestone noticed yesterday:** carry it in the morning summary ("· longest run
  yet"), not a push of its own.
- **Anyone asks for a fourth kind.** The answer starts from the table above, and the
  footer's sentence is a promise to keep.

## Interview angle

> **"How do you decide which notifications an app should send?"**

Start from the user's cost, not the feature list. Every notification spends attention
you were not given, so list every moment the product *could* interrupt, and hold each
to the same tests: is it specific (it says the thing), conditional (sent only when
something is true), one tap from what it names, and free of guilt (no counts against
you, no cadence you fell behind). Most candidates fail one of those: the generic daily
ping, the reach-out reminder, the "what we found" after every note. Keep the few that
pass, default them off, put every switch in one place so "these are the only ones" is
a sentence a user can check, and measure opened and thumbs from the first send so the
set can shrink. Cap the total by construction, with a ledger that can hold one row per
kind per day, rather than by a counter someone has to remember to check.

> **"How did you add a second kind of push without duplicating the first?"**

The first one was built as a ledger with a `kind` column from day one, so the second
is a new kind: the same claim-before-send, the same token lifecycle, receipts and
feedback. What differs is only how the message is composed, so that is the only new
code (`week_facts`, `week_message`, `is_week_due`); delivery was pulled into one
function both call. On the phone, a notification category per kind lets a tap open
the right screen.
