# 0058 — Mood, read from your notes, in Within (Faith, renamed)

**Status:** active
**Date:** 2026-09-30
**Amends:** [0042](0042-faith-a-space-of-its-own.md) (the screen's name, and what it holds)

## Problem

Akin: *can we keep track of the person's mood, somehow, like based on the
conversations.* memo already hears a person most days; how they have been is in those
notes, and nowhere else in the app. The question is how to get it out without a form,
without a diagnosis, and without making something up.

Asked where it should live, Akin folded it under Faith, behind the same switch, and
renamed the screen **Within**: prayer, scripture, and how you've been.

## Options

**How memo learns a mood**

| Option | Buys | Costs |
| --- | --- | --- |
| A daily check-in ("how are you today?"), the industry default (Daylio, How We Feel, Apple Health's State of Mind) | Self-reported, so accurate; no model | A form, every day; memo's promise is that you only ever talk. Days you don't answer are blank |
| **A reader over every note: how the author sounds, with the phrase that shows it** | No extra effort; the history is there from day one | A guess, so it can be wrong; one model call per note while it's on; notes are a biased sample (people write more on bad days, or on good ones) |
| Tone of voice from the audio (Hume-style prosody) | Hears what words hide | Sends raw audio to another provider (the privacy trade we just made Voyage a to-do over); only works for voice notes; no text to show as evidence |

**Where it's stored**

| Option | Buys | Costs |
| --- | --- | --- |
| A `moods` table | Its own shape | A new table: RLS policy, cascade, export, delete-my-data, the reset's classification, all again |
| **Two derivation kinds, `mood` (a note's read) and `mood_day` (your word for a day)** | Everything a derivation already has: RLS, the note-delete cascade (0053), export, delete-my-data, the reset | Every route that lists derivations must leave them out; `NOT_ITEMS` does, and the route sweep now carries them |

**How a day is corrected**, when the read is wrong: a chip on every note (sketched,
declined by Akin: notes stay as they are), or **your own word for the day**, which
outranks every read of it and can be taken back.

## Decision

**A `mood-v1` reader (its own prompt, like faith's) reads every note of someone with
Within on: a valence on five steps (low, heavy, steady, good, bright), up to three
everyday feeling words, the phrase that shows it copied from the note, and a
confidence. "Null" is the usual answer: logistics, ideas, someone else's feelings, and
hard topics told flatly show no mood.** Reads are derivations of kind `mood`; days are
computed on read (a confidence-weighted mean of the day's reads, halves rounded away
from steady), and your own word for a day (`mood_day`, one per day, a partial unique
index) outranks them.

- **Within** leads with "How you've been": this week as seven dots and one sentence made
  from the dots (no model call, so it never says more than they do). Tap a day for its
  notes, the phrase from each, and five choices to say how the day really was.
- **Off with Faith, on with Faith** (`faith_enabled`). Off is `StageOff`, not a skip, so
  the backlog (`read_mood_backlog_task`, the last 30 days, like Habits' 60) reads the
  month when it's turned on.
- **Never an item, never embedded, never in Ask's context.** A guess about a feeling is
  not something the note says; Ask answers from the notes.
- **Guard rails in the schema, not only the prompt:** a clinical word ("depressed",
  "anxiety", "trauma") fails validation and goes back for repair; a valence without
  evidence does too; evidence the note doesn't contain is dropped before it's shown.
- **The rename is a label.** `space = 'faith'`, `faith_enabled`, `/v1/faith` and the
  deep links keep their names: renaming stored values is migration churn for a word
  only people see.

The eval (`evals/mood`, 36 hand-written cases, a CI gate) weighs errors unequally:
precision above recall (a phantom mood on a to-do list is worse than a quiet day), and
**no read may land on the wrong side of steady** (bar 1.00): a grieving note read as good
is the error that would end trust in the screen.

## What we're accepting

- **It's a guess.** The day sheet shows the phrase behind every read so the guess can be
  checked, and one tap overrides it. There is no per-note correction, by choice.
- **The sample is biased.** A week of notes written on bad days reads as a bad week.
  The sentence says "a heavy week", not "you were sad": it describes the notes.
- **A model call per note while Within is on** (the extraction model, like every
  reader): about $0.008 a note on `claude-opus-5`. Haiku is the obvious lever if it
  matters; the eval is how we'd know it still holds.
- **Faith and mood share one switch.** Someone who wants mood without the faith parts
  can't have it. That's Akin's call for his app; it's the first thing to split if memo
  has users who aren't Christian.
- **No month view, no trends, no "what lifts you".** The week, the day, and your word.
  Correlations ("brighter on days with running") are the kind of claim a biased sample
  makes badly.

## Revisit when

- Your own words for days disagree with the reads often (say one day in five over a
  month): the prompt is wrong, or a check-in would serve better.
- memo has users without faith: give mood its own switch.
- Apple Health's State of Mind is wanted: memo could write your own words for days
  there (not its guesses; Health's data is meant to be self-reported). Needs the
  HealthKit write permission, so a native build. Parked in open-questions.
- The eval passes too easily (it does: 1.00 almost everywhere on the starter set):
  replace hand-written cases with real notes whose day you corrected.

## Interview angle

> **"You're inferring something sensitive about users with an LLM. How do you ship it
> responsibly?"**

Make it opt-in, show the evidence for every inference, and let the user's own answer
override it everywhere. Put the guard rails in validation, not only in the prompt (a
banned-word check sends the output back for repair; an unquoted "quote" is dropped), and
weight the eval by harm: a phantom or a wrong-way-round read is worse than a miss, so
they get the tightest bars. Keep inferences out of places where they'd be read as fact
(search, Ask, exports of items).

> **"Why not a separate table?"**

The rows are per-note facts with the same lifecycle as the note's other derived rows:
they must go when the note goes, be tenant-isolated, and appear in export and
delete-my-data. The derivations table already guarantees all of that; a new table would
have to re-earn each one. The cost is discipline in the routes that list derivations,
and a test that sweeps every route against a corpus with the new kinds in it.
