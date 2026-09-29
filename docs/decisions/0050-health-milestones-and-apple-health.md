# 0050 — Health as milestones noticed, from notes and Apple Health, never a training log

**Status:** active
**Date:** 2026-09-29

Amends [0043](0043-habits-noticed-not-logged.md) (its "No Apple Health") and makes the
one exception to [0034](0034-memo-on-the-phone-calendar-and-reminders.md)'s direction.

## Problem

Akin asked for "health/gym" and, asked what that means, said: *workout detail but not
detailed, almost passively based on certain improvements, like milestones.* Then, asked
whether memo should read Apple Health: *yes.*

Habits (ADR 0043) already counts the gym and running days from notes. What it can't do
is notice that a run was the first 10k, or that today's squat was the heaviest yet: it
keeps no numbers. And a workout recorded by the Watch but never talked about isn't
counted at all.

Two pulls, again:

- **Fitness apps are logging apps.** Sets, reps, splits, a form per workout. memo is
  where you say "10k along the canal, new best" and move on.
- **memo keeps no score** (ADRs 0029, 0041, 0043). A milestone must read as a fact about
  what you did, not a badge to chase or a streak to keep.

## Options

**Where the numbers come from**

| Option | Buys | Costs |
| --- | --- | --- |
| A workout log (Strong, Strava): a form per session | Exact, complete | A chore; the opposite of memo |
| **The habits reader keeps the numbers a note gives** (how far, how long, how heavy) | Talking is still logging; no new screen | A prompt version, an eval for invented numbers, and only what you said |
| **Apple Health, read on the phone** | Every Watch workout, with its distance and time, and years of history | A native module, an entitlement, a permission, and memo's first read from the phone |

Both, not either: notes say what Health can't ("squatted 100 kg"), Health has what
nobody says out loud.

**What a milestone is**

| Option | Buys | Costs |
| --- | --- | --- |
| Badges and levels (Apple Fitness awards) | Proven engagement | A score, which memo refuses |
| Personal records per metric (pace, VO2, splits) | Rich | Detail Akin said he didn't want |
| **A few plain facts**: the 10th/25th/50th... day, a first named distance, the longest run/walk/ride/swim, the heaviest weight per lift | Readable at a glance; nothing to maintain | Coarse by design |

**How the phone sends workouts**

| Option | Buys | Costs |
| --- | --- | --- |
| HealthKit anchored queries: only what changed, with deletions | Exact and small | An anchor to persist and reset on the phone |
| Background delivery | Fresh without opening memo | Another entitlement, battery, a wake-up per workout |
| **A window, replaced**: every workout in the last 30 days (two years the first time), on opening memo | Stateless; deletions handled by absence; idempotent | Resends a month of workouts each pass (a few KB) |

## Decision

**The habits reader (now `habits-v2`) keeps the numbers a note gives; with a Settings
switch on, memo reads workouts from Apple Health too; and a tracked habit shows the
milestones it reached, computed on every read, with the newest on Today for two days.**

- **Numbers from notes.** Each activity may carry `amounts`: `distance`, `duration` or
  `weight` (with the lift), in the unit the note used. The model copies the number and
  unit; **code converts** to km, minutes and kg (the model is never asked to do
  arithmetic). Validation refuses a unit that doesn't fit, a weight without its lift,
  and implausible numbers (over 300 km, a day, or 500 kg), which go back for a repair.
  Only numbers the author stated or named ("5k", "a half marathon"); never an estimate
  ("a long run" has none), never pages, reps, calories or body weight.
- **Workouts from Apple Health** (`@kingstinct/react-native-healthkit`, read-only, no
  background delivery). On opening memo, at most every 15 minutes, the phone sends
  `PUT /v1/workouts` with every workout in a window. The server maps HealthKit's
  workout type to a habit key from a fixed table (running, walking and hiking, cycling,
  swimming, yoga and pilates, strength and HIIT as gym, and team and racket sports as
  sport); anything else (golf, sailing, a cooldown) is left in Health. Each workout is
  an ordinary `activity` with no note (`payload.source = "health"`, `payload.health_id`),
  so it counts a day, carries its distance and time, and is removable like any day.
  A partial unique index on `(user_id, payload->>'health_id')` (migration 0021) and a
  per-user advisory lock make two syncs at once file each workout once. A workout
  missing from a window is let go; a day removed in memo stays removed. Only while
  Habits is on (409 otherwise). Turning the switch off deletes every workout memo read;
  signing out turns it off on that phone.
- **Milestones** (`services/milestones.py`), per tracked habit, never stored:
  - *days*: the 10th, 25th, 50th, 100th ... day;
  - *first*: a named distance (5k, 10k, half, marathon; 10/20 km walks; 50/100 km rides;
    1/5 km swims), counted from 98% of the mark, and **only with something shorter known
    before it**, so where memo's knowledge starts is not mistaken for a first;
  - *longest* run/walk/ride/swim and *heaviest* per lift: the standing record, only with
    at least three before it and a 2% margin, so the second run ever isn't a record and
    a hair further doesn't re-date it.
  Shown on the habit, newest first, each opening its note; the newest from the last two
  days is one line on Today.

## What we're accepting

- **memo reads from the phone now.** The rule in ADR 0034 was about the calendar, and
  stays; Health is the exception Akin chose. Workout data is health data: it's sent only
  while two switches are on, only workouts (no heart rate, no sleep, no steps), and
  deleted when the switch goes off.
- **"First" means the first memo knows of.** Health usually gives years of history, so
  it's true in practice; from notes alone it's what you've said since Habits was on. The
  "something shorter before it" rule stops the worst case, not every case.
- **Duplicates across apps.** A run recorded by the Watch and imported by Strava is two
  workouts; days don't double-count, and the evidence shows both.
- **A deletion inside Health reaches memo within 30 days.** One older than the window
  isn't noticed.
- **Every note is read a little longer** (the prompt grew), and the eval now has an
  amount precision bar: an invented number is a false record, the silent failure again.
- **No pace, no "fastest 5k".** Duration is kept for display and for later.

## Revisit when

- **Akin asks for speed**: a fastest 5k needs distance and duration on the same effort,
  which both sources already give.
- **Sleep or steps are wanted**: "early night" could come from Health's sleep, but that
  needs a bedtime rule, not a mapping.
- **The window resend shows up in the logs**: switch to anchored queries.
- **A milestone feels like pressure** rather than a fact: fewer kinds, or off Today.

## Interview angle

> **"How would you add fitness tracking to an app that isn't a fitness app?"**

Don't add a logging screen. Derive the data from what users already produce - what they
say, and what their watch already recorded - and keep it attached to its evidence so
every number is explainable and removable. Let the model extract, not compute: copy the
number and unit, convert in code, validate ranges, and measure the costly error, which
is an invented number, because it produces a false record that no one notices. Sync the
device data idempotently (a replaced window plus a unique index is simpler than a
change feed until volume says otherwise), and compute derived facts on read so they
follow every correction for free.
