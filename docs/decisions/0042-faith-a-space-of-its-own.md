# 0042 — Faith: a space of its own, prayers that get answered, a reading plan that never falls behind

**Status:** active (built 2026-09-29; see "What changed when it was built")
**Date:** 2026-09-29

## Problem

Akin wants memo to hold his Bible study and his relationship with God: what he prays
for, what God has done, what he's reading and what it's teaching him. Three things make
this different from the rest of memo:

- **It's the most personal material memo would hold.** It shouldn't turn up in Today
  between a dentist appointment and a work todo unless he wants it there.
- **Faith apps default to streaks** ("Day 41!"), where a missed day becomes guilt. memo
  already refuses to keep score (ADRs 0029, 0041). That rule matters more here, not less.
- **The Bible text itself is licensed.** He reads the NIV, which is copyrighted.

## What Akin chose (2026-09-29)

From eight ideas offered, he picked five:

| # | Idea | In |
| --- | --- | --- |
| 1 | A Scripture page: every passage you've written about, by book | No. Its engine is needed anyway; see below |
| 2 | **Prayers** as their own kind of item, answered rather than done, linked to people | Yes |
| 3 | **Remembering**: answered prayers come back a year on ("A year ago you prayed about the new job. Answered in March") | Yes |
| 4 | **From your own history**: now and then, a passage you wrote about, with what you wrote | Yes |
| 5 | Ask about your walk ("what have I learned about forgiveness?") | Not now |
| 6 | Sermon recording with passages and key points pulled out | Not now |
| 7 | **A reading plan** that shows where you are, never how far behind | Yes |
| 8 | **A faith section in the weekly review**: what you read, prayers answered | Yes |

Also: **NIV to start, with the translation changeable**, and **kept in its own space**.

## Options

**Where it lives**

| Option | Buys | Costs |
| --- | --- | --- |
| Woven into everything (prayers in Today, passages in the Home list) | One place to look; no new navigation | Faith sits next to errands; no control over where the most personal notes appear |
| **A Faith space: its own screen, its own top card, its own section in the weekly review** | A boundary Akin chose; Home stays as it is | A second place to look; notes have to be filed into the right space |
| A separate app | The cleanest boundary | Duplicates capture, auth, sync and review; gives up memo's whole pipeline |

**How the Bible text is shown**

| Option | Buys | Costs |
| --- | --- | --- |
| Show NIV text inside memo | Read and write in one place | Needs a licence from the NIV's publisher. Free quotation is limited to a small number of verses, and a reading plan means whole chapters |
| Show a public-domain translation (BSB, WEB, KJV) | Free to show, no limits | Not the translation he reads |
| **References only in memo; tapping one opens it in his Bible app (a bible.com link, which opens YouVersion), in the chosen translation** | No licence, and he reads there already | Reading happens outside memo, so memo can't tell a chapter was read; he marks it himself |

**How notes get into Faith**

| Option | Buys | Costs |
| --- | --- | --- |
| Only notes captured from the Faith screen | Never misfiled | A quick "pray for Tunde" said from Home is lost to Faith |
| Only the reader decides | No choosing, just talk | Misfiling both ways: a work note that says "pray this lands" |
| **Both: anything captured in Faith is faith; elsewhere, the reader files prayers and passages into Faith** | Deliberate by default, forgiving when rushed | The reader can still misfile; the prayer card has to be easy to move |

**How the reading plan keeps time**

| Option | Buys | Costs |
| --- | --- | --- |
| Dated ("day 142 of 365") | The industry default; easy to explain | Every missed day is "behind", which is the streak by another name |
| **By position: the next reading is always the one after the last one marked read** | A week away is a week paused, not a debt | No "on pace to finish by December"; deliberately |

## Decision

**A Faith space of its own, reached from Home, holding prayers, remembering, readings and a
plan, with references that open in the NIV (changeable) in his Bible app, and no score
anywhere.**

- **Prayers (2)** are a new derivation kind, `prayer`: a title, optionally a person, and
  once answered, `answered_at` and a line about how. You don't tick a prayer; you mark it
  answered. Open ones are never "overdue". A prayer linked to someone also shows on that
  person's page.
- **Remembering (3)** lives in Faith's own top card: at most one a day, an answered prayer
  from a year or more ago, chosen the same way On this day is (ADR 0040).
- **From your own history (4)** shares that card: on days with nothing to remember, a
  passage he wrote about, with what he wrote. One item, never a feed.
- **The reading plan (7)** is position-based. Straight through the Bible is 1,189
  chapters, 3–4 a day for a year. memo generates the plan itself from the chapter counts,
  so there's nothing to license. "You're in Exodus 12", today's reading, a tap to open it,
  a tap to mark it read.
- **The weekly review (8)** gains a separate Faith section at the end: chapters read,
  prayers answered, passages written about. Facts, nothing to beat.
- **Passages are recognised under the hood.** Ideas 4 and 8 need memo to know that a note
  is about Romans 8, which is idea 1's engine without its page. The model spots a
  reference; deterministic code turns "1 Cor 13", "First Corinthians 13" and
  "1Co 13:4-7" into one canonical reference, checked against real chapter and verse
  counts. The model never produces a reference memo trusts unchecked, and it never
  produces Scripture text at all.
- **Translation** is a setting, NIV by default. It decides which version the links open.
  Showing text inside memo is a separate, later decision.

## What we're accepting

- **The NIV text never appears inside memo.** Reading happens in another app, so "read"
  is a tap Akin makes, not something memo observes. If that tap gets forgotten, the plan
  just waits.
- **A second place to look.** Anything in Faith is out of sight on Home, by design.
- **The reader will sometimes misfile.** A "pray for" inside a work note lands in Faith;
  a reflection that names no passage stays out of it.
- **A broader extraction prompt puts todos and events at risk.** Adding two kinds to the
  reader can lower its precision on the existing ones. The extraction eval is a merge gate
  and must stay green, with new cases for prayers and passages.
- **"Mark" and "John" are both people and books.** "Mark said…" versus "Mark 4" is exactly
  where people and passages collide. It needs its own eval cases, and a wrong guess costs
  either a bogus person or a lost passage.
- **Schema changes, all additive:** a `space` on captures, `prayer` and `passage` added to
  `ck_derivations_kind`, `ck_derivations_person_only_on_mentions` relaxed so a prayer can
  name a person, and a table for the reading plan's position. Another extractor version.

## Open, with the defaults used until Akin decides

1. **Faith notes and the rest of memo.** Default: faith notes live only in Faith. They're
   not in Home's list, Home search, On this day or Home's Ask.
2. **Which reading plan.** Default: straight through, Genesis to Revelation. Alternatives:
   chronological, or New Testament plus Psalms. All can be generated from chapter counts.
3. **Which Bible app the links open.** Default: bible.com, which opens YouVersion if
   installed. The NIV's version id there is to be verified when built.

## What changed when it was built (2026-09-29)

Akin asked for it to be built, and for Faith to be **a feature turned on in Settings**.
That, and building it, changed six things above. None of the original reasoning was wrong;
these are the details that only building settles.

1. **Faith is a switch, off by default, on the account** (`users.faith_enabled`). It has
   to be on the server, not the phone: the server is what keeps faith notes out of Home,
   search, Ask and On this day, and it does that **only while Faith is on**. Turned off,
   nothing is deleted and faith notes are ordinary notes again, so no note is ever lost
   behind a switch. The translation (`users.bible_translation`, NIV by default) lives
   beside it.
2. **The three open decisions took their defaults:** faith notes live only in Faith
   (while it's on), the plan goes straight through Genesis to Revelation (370 readings,
   none crossing a book), and links open bible.com, which hands over to YouVersion. The
   bible.com version numbers were checked in a browser (NIV is 111).
3. **Finding a passage is code alone, not model plus code.** `pythonbible` (MIT) parses
   and validates references against real chapter and verse counts; memo adds two rules:
   a reference must name a chapter ("Mark said" is a person, "Mark 4" is a book), and
   spoken forms ("John chapter 3 verse 16", "Psalm chapter twenty three") are rewritten
   first. That makes the Mark/John collision a unit test instead of an eval case, and
   costs no model call. Passages are found in faith notes only.
4. **Not built yet: the reader filing prayers and passages from notes written elsewhere.**
   Doing it properly means a new extraction prompt version, new eval cases and the gate
   staying green, which is its own piece of work. For now a note goes into Faith by being
   written or recorded there, or moved there from its page ("Move to Faith"). Prayers are
   added by hand.
5. **Reflections don't make ideas.** The extractor still runs on a faith note, and its
   todos and events are kept (they're commitments), but its ideas are dropped: a
   reflection is full of thoughts, and Home's Ideas shouldn't fill with them.
6. **Prayers and passages are never embedded,** so they answer nothing in Home's Ask, and
   the generic derivation routes refuse them. Related notes stay within a space while
   Faith is on.

The schema landed as planned in migration 0016: `space` on captures and on chunks (copied,
like `user_id`, so retrieval filters on the chunks table alone), two new kinds, the
person check widened to prayers, and `faith_readings` with row-level security. Export
includes the readings, and "delete all data" removes them.

## Revisit when

- **Akin wants to read inside memo:** that's the licence question for NIV, or a
  public-domain translation shown alongside.
- **More than about one in ten auto-filed items land in the wrong space:** drop automatic
  filing and file only what's captured from Faith.
- **The plan's position starts to feel like a score anyway** ("still in Leviticus"): show
  less.
- **Habit tracking** (open question Q11) arrives: prayer and Bible reading are likely its
  first habits, and the two must not count the same thing twice.
- **Akin says a prayer said in a Home note got lost:** build item 4 above, with its eval
  cases.

## Interview angle

> **"How would you add a sensitive domain, like faith or health, to a general-purpose app?"**

Give it a boundary the user chose, rather than mixing it into every feed. Decide the
licence question for third-party content early: linking out costs nothing, while showing
the text is a contract. Let the model detect entities, and let deterministic code turn them
into canonical, validated forms, because a model that is usually right about "1 Cor 13:4" is
still wrong often enough to matter. And measure progress by position, not by days in a row,
because a streak turns a missed day into a debt, and that pushes people away from exactly
the practice you meant to support.
