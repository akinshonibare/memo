# 0008 — One capture primitive, everything else is a derivation

**Status:** accepted — **load-bearing; breaking this breaks the project**
**Date:** 2026-08-17

## Problem

memo's roadmap adds habits, faith, health, relationships, calendar, reconciliation. In a
normal app each of those becomes a table, a module, a screen, and a migration. Five
features in, the app is five apps sharing a database, and cross-cutting questions ("what
was going on the week my sleep got bad?") become joins nobody wants to write.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Feature-per-table** (`todos`, `events`, `habits`, `people`, …) | Obvious schema. Each feature's queries are simple and fast. Constraints are precise per type. Standard, boring, easy to onboard to. | Capture-time categorisation: the user must decide *what kind of thing* they're writing before writing it. That single requirement is what kills journaling apps — friction at the exact moment you need zero. Cross-type questions need N-way joins. Every new feature is a migration + module + screen. |
| **Fully schemaless** (one `documents` table, JSON blob) | Maximum flexibility, no migrations ever. | No constraints, no types, no query planner help. Every read is defensive parsing. Rots within months. |
| **One primitive + typed derivations** (`captures` → `derivations` → `chunks`) | One write path — capture never asks "what is this?". Extraction sorts it *after*, so friction is zero at the moment that matters. A new feature = a new derivation `kind` + a query, no schema change. Every derivation points back at its capture, so citations and "why did you think that?" are free. Re-extraction is possible: improve the extractor, re-run over history, get better derivations from the same raw captures. | Reads are less direct — "today's todos" is a filtered query over a polymorphic table, not `SELECT * FROM todos`. Typed fields per kind have to live in a JSON payload column or a per-kind side table. Indexing a polymorphic table takes thought. The correctness of *everything* now depends on extraction quality. |

## Decision

One primitive, many views — because zero-friction capture is the product, and because
keeping raw captures immutable means every future extraction improvement applies
retroactively to everything already written.

## What we're accepting

**Extraction quality becomes the single point of failure.** If extraction is bad, todos are
missing, and the user stops trusting the app — and unlike a typed-form app, there's no
manual fallback path by default. This is precisely why the brief makes the eval harness a
Phase-1 requirement and mandates showing extraction results immediately with one-tap
correction. Those aren't nice-to-haves; they are the mitigation for this ADR's downside.

We accept more complex reads and polymorphic indexing. We accept that per-kind validation
lives in Pydantic models over a payload column rather than in database constraints — so
the database will happily store a malformed derivation if the application lets it.

## Revisit when

**Don't.** If a future feature seems to need its own table, that is the signal to re-read
this ADR, not to add the table. The correct move is almost always a new derivation `kind`.

The one legitimate exception: an entity that is genuinely *not* derived from a capture and
has its own lifecycle — a `people` table in V5, where a person exists independently and
capture mentions *link to* it. Note the shape: that's a new entity that derivations point
at, not a new capture-like primitive. The rule being protected is "one write path in", and
that stays intact.

## Interview angle

> **"How do you design a schema for a product whose features you don't know yet?"**

Find the invariant. Here it's "the user produces timestamped content, and meaning is
extracted from it" — that's stable across every roadmap item, while "todo", "habit", and
"health note" are all just interpretations. So model the invariant as the primitive and
make interpretations derived, typed, and pointing back at their source.

Two properties this buys that are worth naming unprompted:
1. **Retroactive improvement.** Because raw captures are immutable and derivations are
   regenerable, improving the extractor improves your entire history — not just new data.
   That's only possible if you never destructively transform input on the way in.
2. **Provenance for free.** Every derivation knows its source capture, so citations,
   "why did you think that?", and correction UX all fall out of the schema instead of
   being bolted on.

The cost you must state, or the answer sounds naive: you've moved correctness from the
schema into the extractor, so you now need evals the way a typed-schema app needs
constraints.
