# 0044 — Readings you choose: set what you're reading, log what you read, no fixed plan

**Status:** active
**Date:** 2026-09-29

Supersedes the reading plan in [ADR 0042](0042-faith-a-space-of-its-own.md) (choice 7).
The rest of 0042 stands.

## What changed

0042 built a reading plan memo chose: straight through the Bible, Genesis to Revelation
in 370 readings, kept by position so a week away was a week paused. When Akin opened
Faith on the phone, it said "Genesis 1–4" and he couldn't change it. His words: Faith is
"not meant to be a walkthrough but a reference when I talk in my notes", and "I need to
be able to set what I'm reading, don't hard code it".

The premise that broke: that Akin wanted memo to choose his reading. Position-based
timekeeping was right (no "behind"), and he still wants reading counted in the weekly
review and in the Bible habit (ADR 0043). What was wrong was memo deciding *what*. That
was a misjudgement, not a change in the world: 0042 listed "which reading plan" as open
and shipped a default that turned out to be a decision.

## Problem

Keep what reading gives Faith - a line in the weekly review, days for the Bible habit, a
quick way to open today's chapter - without memo choosing the chapter.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Fixed plan, as built (0042) | Zero setup; the industry default (YouVersion plans) | memo chooses; Akin's reading happens elsewhere, so the plan is fiction |
| Pick from several plans (chronological, NT in 90 days...) | Still zero thought | Still memo's list; more data to maintain; the same objection |
| **Set what you're reading; log what you read** | Whatever Akin is actually reading; memo only offers the next chapter of it | One line to type once; memo can't know he finished a chapter unless he taps |
| Log only, nothing set | Simplest | Every reading is typed; no "next chapter", no one-tap Read |

## Decision

**You set what you're reading - a book ("John"), some chapters ("Romans 8"), or verses -
and memo offers the chapter after the last one you read in it. "Read" logs that; "I read
something else" logs any passage; Undo takes the last one back.**

- **What you're reading** is `users.bible_reading`, a passage in the same shape as a
  passage found in a note. Typed, because "John" is quicker to type than to pick; a bare
  book name is allowed here (it can only mean the book), unlike in a note.
- **A reading** is a derivation of kind `reading`: the passage and `read_on`, no note, once
  per passage per day. Not an item, never embedded (like prayers and passages).
- **Next** is the chapter after the last reading inside what you set, else its first
  chapter; none once you've read to its end, and the card asks what's next. Changing what
  you're reading keeps every reading.
- **The weekly review** lists the week's readings; **the Bible habit** counts their days,
  with notes that name a passage.
- **Faith is also a reference:** a new section lists every passage your notes name, most
  recently first, with how many notes and the notes themselves, and a link to read it.
- **Migration 0018** turns each old plan reading into the passage it stood for, on the
  local day it was marked, then drops `faith_readings`. The plan's arithmetic is frozen in
  the migration so it never depends on app code that no longer exists.

## What we're accepting

- **Reading is still a tap.** memo can't see the Bible app; a chapter read and not
  tapped isn't counted (it is if a note names it).
- **"Next" is one chapter.** Three chapters in a sitting is "I read something else",
  typed as "John 3–5".
- **Downgrading 0018 loses readings:** a reading of any passage has no position in a plan
  to go back to. Stated in the migration.
- **Rollout order matters:** the app update ships before the server, because the app on
  phones today reads the old plan field and would break on the new response. The new app
  tolerates the old server (no reading card until the server catches up).

## Revisit when

- **Akin follows a published plan** (a church or group plan): import its list as a
  sequence of "next" readings, still his choice, never memo's default.
- **Typing a passage gets in the way:** a book-and-chapter picker.
- **Readings and passages named in notes start to duplicate** in the habit or the review:
  treat a note naming a passage on the same day as that day's reading.

## Interview angle

> **"You shipped a default and the user said it was wrong. What did you do?"**

Separate the part of the decision that held from the part that didn't. Position-based
timekeeping (no "behind") held; memo choosing *what* to read didn't, because it replaced
the user's real activity with a fiction. Keep the outputs other features depend on (the
review, the habit), move the choice to the user, and migrate existing data into the new
shape instead of dropping it - freezing any logic the migration needs inside the
migration, so it still runs after the app code is gone. And ship the client before the
server when the response shape changes, with the client tolerant of both.
