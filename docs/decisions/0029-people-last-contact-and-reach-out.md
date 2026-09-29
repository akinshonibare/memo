# 0029 — People: who a note is about, when you last spoke, and when to reach out

**Status:** active
**Date:** 2026-09-28

## Problem

Akin asked for memo to keep track of when he last spoke to people, to remind him to
reach out, to show every note about a person, and, when a name could be more than one
person, to ask which one via last name or another alias.

There are four problems here, and the hard one is not the reminder:

1. **Identity.** "Tunde", "Tunde Bakare", "T" and "my cousin" may be one person; two
   notes saying "Tunde" may be two. This is entity resolution, which the brief calls the
   hardest problem in the system (V4).
2. **Contact vs mention.** "Had lunch with Tunde" is contact. "Ada says Tunde got the
   job" and "need to call Tunde" are not. If mentions count, Tunde's last-contact date
   keeps moving and the reminder never fires, with nothing looking wrong. This is the
   silent failure.
3. **A person view**: every note, plus what is still open with them.
4. **The reminder.**

The brief already placed this at V5 ("relationship layer: person entities, threads,
state-based nudges (not cadence-based)"), and ADR 0008 names a `people` table as the one
legitimate exception to "every feature is a derivation kind".

## Options

### Where people live

| Option | What it buys | What it costs |
| --- | --- | --- |
| Name-only derivation kind, grouped by normalised name | No new table | Two Tundes collapse into one; no aliases; nowhere to store a rhythm. Fails the stated requirement. |
| `@Tunde` tagging at capture (Notion / Obsidian; the industry default for notes apps) | Deterministic, no model | Asks the user to categorise at capture time, the thing ADR 0008 exists to prevent; impossible by voice. |
| **`people` table + `mention` derivations pointing at it** | ADR 0008's named exception: a person has their own lifecycle (aliases, rhythm), notes point at them. A composite FK `(person_id, user_id)` keeps tenancy in the data layer (ADR 0010). | A second entity to keep consistent: merge, split, prune, export, account deletion, RLS. |

### Where resolution happens

| Option | What it buys | What it costs |
| --- | --- | --- |
| Inside the extraction prompt, with the people list in context | Uses context ("Tunde from work") | Extraction output depends on database state, so the eval stops being reproducible; the prompt grows and its cache breaks with every new person; the model can invent an id. |
| LLM adjudication for every ambiguous name | Fewer questions | A model call per ambiguity, and a model that picks between two Tundes is a model that can silently merge them. |
| **A separate people stage: a stateless model read, then a deterministic resolver that asks when unsure** | The read is evaluated on its own and can run over old notes without re-extracting (and duplicating) their todos. The resolver is cheap, re-runnable, and never guesses. | A second model call per note (a few dollars a month at memo's volume). An ambiguous name costs the user a tap. |

### The reminder

| Option | What it buys | What it costs |
| --- | --- | --- |
| None: a People list sorted by last contact | Nothing to get wrong | The user asked for reminders. |
| State-only: surface follow-ups the notes imply ("ask Ada how the surgery went") | Truest to the brief | Misses the people you simply drift from, which is the thing asked for. |
| **Cadence, default monthly, editable per person; in-app, capped, with context** | What was asked for | Amends the brief's "not cadence-based" line; see below. |
| Cadence with push | Reaches you when the app is closed | Overrides ADR 0023; needs APNs, a token table and a delivery cron. |

## Decision

**A `people` table and `mention` derivations; a people stage that reads each note with
its own prompt (`people-v1`) and resolves names deterministically, asking the user
whenever more than one person fits; last contact computed from contact mentions; a
monthly reach-out rhythm by default, per person, shown in Today.**

The pieces:

- **Reading** (`prompts/people_v1.py`, `PeopleReader`): each person once per note, with
  `name` as written, `full_name` if given, a `hint` that tells same-named people apart,
  `contact` (`in_person | call | message | unspecified | none`), `occurred_on`
  (resolved; defaults to the capture date), and `context`. A plan or an intention is
  always `none`. A contact dated after the note fails validation into the repair loop.
- **Resolving** (`services/people.resolve`): names are compared after normalising case,
  accents, punctuation and a leading "my". One name *fits* another when all of its words
  appear in the other ("Tunde" fits "Tunde Bakare"; "Tunde Ade" does not). Exactly one
  fit links. Two or more fits ask. No fit but a near spelling ("Tunday") asks. Nothing
  at all creates a new person. The fuller name is used when given, so "Tunde Ade" can
  settle a two-Tunde question on its own.
- **Asking**: an unresolved mention has no `person_id`; its candidates are computed when
  read, not stored, so they stay right after merges. The app asks "Which Tunde?" with
  each candidate's note ("from work") and last contact, plus *Someone new*, *Someone
  else* and *Not a person*. An answer that names memo didn't match ("Mum", "Tunday")
  becomes an alias, so the next note links on its own.
- **Last contact** is `max(occurred_on)` over non-rejected mentions whose contact is not
  `none`, computed per request. A stored column would have to follow every review, edit,
  merge and rejection, and would drift.
- **Reach-out**: every new person starts at 30 days (Akin's call), editable per person,
  or off. The clock starts at the last *contact*, so someone only ever mentioned is never
  suggested. Today shows at most three people, most past their rhythm first, each with
  the latest thing a note said about them. *Spoke to them* logs a contact (iOS gives no
  app your calls or messages); *Not now* snoozes for a week. No counts, no badges, no
  push.
- **Integrity**: RLS on `people`; the composite FK means a mention can only point at its
  own user's person; a CHECK keeps `person_id` on mentions only. Resolution takes a
  per-user advisory lock (`pg_advisory_xact_lock`), because it is check-then-insert: two
  notes naming a new "Kemi" read at once would otherwise make two Kemis. A test proves it
  by removing the lock and watching it fail.
- **Contract with installed apps**: mentions are left out of `/v1/derivations` and out
  of a note's `derivations`; they come in a separate `people` field. A build from before
  this ADR never sees a "mention" item in its Planner.
- **Old notes**: a cron reads people in notes written before the stage existed, 40 at a
  time, newest first, using the same `job_runs` ledger as the pipeline, so each note is
  read once however it gets there. A version bump does not re-read history.
- **Ask** (`answer-v2.3`) sees people lines: "person: Tunde Bakare · spoke on the phone
  on Sun 20 Sep 2026", or "mentioned, not in touch".

## What we're accepting

- **memo only knows what it is told.** "Last spoke" means "last time a note or a tap said
  so". A call you never mention makes a reminder come early. *Spoke to them* is the
  mitigation, not a fix.
- **Splits over merges, by design.** "Dr Okafor" does not link to "Chinedu Okafor", and
  "T" does not link to "Tunde": each becomes someone new, fixed by one merge. Titles are
  kept because stripping "Uncle" would merge Uncle Tunde into your friend Tunde.
- **The single-candidate trap.** While you know one Tunde, every "Tunde" links to him,
  including the first mention of a second Tunde. The link is visible on the note and one
  long-press to change, but it is not asked about. The resolver has no way to know a
  second Tunde exists until you say so.
- **A cadence reminder, against the brief's wording.** The brief says nudges should be
  state-based, not cadence-based, with no guilt mechanics. Akin chose a default monthly
  rhythm. What keeps it on the right side of "no guilt": the clock only starts at real
  contact, three at most, a reason attached to each, no counts anywhere, and a one-tap
  "not now". The brief is amended to say so.
- **A second model call per note**, and the backfill's one-off cost (one call per old
  note). Both are recorded in `llm_calls` under stage `people`.
- **Phantom transcriptions.** "Tunde" transcribed as "Tunday" becomes a question, not a
  link. Feeding people's names to Deepgram as keyterms (the reason for Deepgram, ADR
  0013) would prevent many of them; not done yet.
- **Data about other people** who never agreed to be in a journal. It is in export, in
  delete-everything and account deletion, and behind RLS like everything else.

## Revisit when

- **Questions get tiresome.** Count "which one?" answers per week. If more than a few a
  week are answered with the obvious candidate, add model adjudication for ambiguous
  names, evaluated against `evals/people/resolution.jsonl` with wrong links held at zero.
- **The reading gate fails on real notes.** Contact precision under 0.95 means reminders
  are being silently suppressed: fix the prompt before anything else.
- **Reach-out goes unused for two weeks** (the brief's ship test), or is dismissed more
  than acted on: change the default to off, not the cap.
- **Push arrives (V4)**: reach-out is a candidate for the one-great-nudge-a-week, with the
  act-on-rate metric from day one.

## Interview angle

> **"How would you do entity resolution for people mentioned in free text?"**

Split it into reading and resolving, because they fail differently. Reading is a model
problem, evaluated like extraction. Resolving is the classic pipeline:
**blocking** (cheap candidate generation: normalised names, aliases, near spellings),
**matching** (here, a strict rule: one name's words inside the other's), and a
**decision policy** with a human in the loop for the ambiguous middle.

The point to make unprompted is the **asymmetry of errors**. A false merge silently
corrupts two histories and nobody notices; a false split is visible and one merge fixes
it. So you tune for precision, prefer splits, make merges reversible or at least cheap,
and let the user answer only the genuinely ambiguous cases. Every answer is a labelled
example, and learned aliases make the next one automatic.

Two details that show you've built it: resolution is **check-then-insert**, so it needs
a lock (or a unique key, which you can't have because two people can share a name); and
"last contacted" must be **derived, not stored**, because it depends on reviews, merges
and rejections that happen after the fact.
