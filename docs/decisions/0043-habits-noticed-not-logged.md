# 0043 — Habits: noticed in your notes, suggested when they repeat, counted by rhythm

**Status:** active; amended by [0050](0050-health-milestones-and-apple-health.md): activities
keep the numbers a note gives, milestones are shown, and workouts can come from Apple Health
**Date:** 2026-09-29

Resolves [open question Q11](../open-questions.md).

## Problem

Akin wants to track habits - the gym, reading the Bible, talking to family, reading,
"and others" - and asked specifically whether memo could **notice** them rather than be
told. Two pulls:

- **Habit apps are logging apps.** A checkbox a day, and a streak that a missed day
  breaks. memo is a capture app: the gym session is already in this morning's voice note.
  Asking for a tap as well is a chore memo shouldn't need.
- **memo keeps no score** (ADRs 0029, 0041, 0042). The streak, the industry default, is
  exactly the score memo refuses.

## Options

**How a habit gets counted**

| Option | Buys | Costs |
| --- | --- | --- |
| Tap to log (Streaks, Habitify) | Exact, simple, the default | Ignores memo's whole point; a second place to record a day already described |
| **Declared habits, counted from notes, a tap as a backup** | Talking is logging; the note is the evidence | A reader, and its eval, with a high bar for false counts |
| Fully inferred, nothing declared | No setup | Vague: you can't say what matters to you |

**How a habit is shown**

| Option | Buys | Costs |
| --- | --- | --- |
| Streak ("Day 41") | The best-known motivator | A missed day becomes a debt, and people quit at the break |
| **Rhythm: "2 of 3 this week", "last time: 2 days ago", eight weeks at a glance** | Nothing to break | Weaker pressure, on purpose |

**Where "talking to family" and "reading the Bible" come from**

| Option | Buys | Costs |
| --- | --- | --- |
| The habit reader reads them too | One mechanism | Counts contact twice (People already reads it) and disagrees with People |
| **Computed from what memo already knows** - People's contacts with family, Faith's readings and passages | One record shown in two places; no new reading | memo has to know who is family |

## Decision

**Habits are a Settings switch, off by default. With it on, a `habits-v1` reader picks
out what you actually did in each note; memo suggests a habit once the same activity
shows up on 3 days in 3 weeks; a tracked habit is counted by rhythm, never by streak.**

- **The reader** (`prompts/habits_v1.py`, `evals/habits`) returns activities *done*: a
  key from a short vocabulary (gym, running, walking, cycling, swimming, yoga, sport,
  reading, bible, prayer, journalling, meditation, cooking, music, study, early night)
  or a short free-form one ("gardening"), and the date it happened. A plan, a wish or a
  negation ("I should hit the gym", "gym tomorrow", "skipped the gym") is not an
  activity. Talking to people is not an activity: People reads that. Each activity is a
  derivation of kind `activity`, with the note as its evidence.
- **Two habits are computed, not read:** *time with family* is the days People recorded
  real contact (met, called, texted) with someone who is family; *reading the Bible* is
  the days a reading was marked in Faith, a faith note named a passage, or the reader
  saw "read my Bible". Who is family: a person you marked, or else memo's guess from a
  relationship word in their name or note (Mum, Dad, brother, sister, aunt, cousin, ...).
  A person page has the switch to correct it.
- **Suggestions:** one at a time, on Today and the Habits screen. An activity (or family
  contact, or Bible reading) on at least 3 different days in the last 21, not already
  tracked or dismissed. "Not now" dismisses that suggestion for good.
- **History comes with it.** Old notes (60 days back) are read in the background once
  Habits is on, so accepting "Gym" shows every gym day memo already knew about. For
  that to work, a note written while Habits is off leaves **no** ledger row for the
  stage (`pipeline.StageOff`): "succeeded" means read, and a skipped note marked
  succeeded would never be picked up by the backlog.
- **Counting:** a habit counts *days*, one per day however many notes mention it. A tap
  ("Log today") adds a day; a wrong day is removed with a tap (its activities are
  rejected: the precision label). Each habit shows this week against an optional target
  ("3 a week"), the last time, and eight weeks of bars. Nothing is overdue and nothing
  resets.
- **A gentle nudge:** with a target set, when it's been more than twice the usual gap
  (a 3-a-week habit: 6 days), Today says "Gym: last time 6 days ago", at most one, and
  only when there's no suggestion to show.
- **The weekly review** gets a Habits section: each habit's days that week.

## What we're accepting

- **Every note is read** for someone with Habits on (activities can be anything, so no
  word gate as for Faith): about a cent a note, a few dollars a month at Akin's volume,
  plus a one-off read of the last 60 days. None for anyone with Habits off.
- **A false count is the silent failure:** "gym tomorrow" counted as a gym day makes the
  numbers look better than the truth, and nothing looks wrong. So precision has the
  highest bar in the eval, and every count shows the note behind it.
- **Family is a guess until corrected.** "Tunde" is never guessed to be family; a cousin
  only if the note or name says so.
- **Rhythm motivates less than a streak.** Chosen: calm over pressure.
- **No Apple Health.** memo writes to the phone and never reads from it (ADR 0034); a
  workout that was never written down is not counted. *(Reversed by ADR 0050, at Akin's
  choice, the same day.)*

## Revisit when

- **Precision on real notes falls below the bar:** tighten the prompt before anything
  else.
- **The cost matters:** the eval can say whether a smaller model reads activities as
  well; habits don't need the extraction model's reasoning.
- **Akin wants quitting habits** ("less phone at night"): times per week trending down,
  never "days since".
- **Apple Health**, only if Akin changes the memo-writes-only rule.

## Interview angle

> **"How would you build habit tracking that doesn't feel like a chore?"**

Derive the check-ins from data the user already produces instead of asking for them, and
keep the evidence attached so every count is explainable and correctable. Reuse signals
you already have (contacts, reading history) rather than reading the same thing twice.
Measure the classifier on the costly error, which here is counting an intention as done,
because inflated numbers are wrong in a way no one notices. And show rhythm rather than
streaks: a streak turns one missed day into a reason to stop.
