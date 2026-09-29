# 0049 — A map of one thing, not a map of everything

**Status:** active
**Date:** 2026-09-29

## Problem

Akin asked for a mind map. memo already keeps the links one would draw: who a note is
about (mentions, ADR 0029), what was taken from it (items, prayers, passages), notes
about the same thing (related, ADR 0028), and, since ADR 0048, what a note answers or
carries on. None of them can be seen together: each lives on its own screen.

The question a map answers is usually "what's connected to *this*?": everything around
Kemi, everything that came of that coffee, where the pricing idea has been.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| **Everything at once** (a force-directed graph of every note, as in Obsidian) | The picture people imagine | A hairball past a few dozen notes; needs a canvas (Skia), which is a native build, and the free build quota is used up this month |
| **Themes**: cluster notes by embedding, name each cluster with a model, draw clusters and their links | Insight nothing else gives | A model call to name clusters, an eval for whether the names are right, and enough notes for clusters to mean something |
| **A map of one thing** (an ego-graph): one node in the middle, what's linked to it around it; tap a neighbour to move the middle there | Readable at any size; every edge is a link memo already has; walking it is the exploration | Never shows the whole; a busy person's forty notes are cut to eight |

## Decision

**A map of one thing.** `GET /v1/map?kind=person|note|idea|prayer&id=…` returns the
middle and at most 14 neighbours, each with the relation in words. No new data and no
model: the service reads mentions, items, prayers, passages, confirmed updates and the
related-notes query (now shared with the note screen).

- **Person:** notes about them (8 newest), prayers for them, people they come up with.
- **Note:** who it's about, what came from it, prayers, what it answers or carries on,
  related notes, passages.
- **Idea:** the note it came from, the notes that carried it on, who it's about,
  nearby ideas.
- **Prayer:** who it's for, where it was prayed, where it was answered, other prayers
  for them.
- Groups are **interleaved** up to the cap, so a busy person still shows their prayers
  and people, not just notes. Faith notes stay off the map while Faith is on, as they
  stay off Home (ADR 0042).
- **The app** (`MapScreen`) draws it with plain views: nodes on a superellipse around
  the middle, lines as thin rotated views, no drawing library, so it ships as an OTA
  update. Tap re-centres; hold opens the thing; back walks back through the middles
  visited. Reached from a person, a note, and each idea.

## What we're accepting

- **No whole picture.** The map answers "what's around this?", not "what does my
  journal look like?". Themes are the answer to the second, later.
- **Fourteen at most.** A person with forty notes shows eight of them; the Person
  screen has the rest.
- **The "related" and "near" links inherit the 0.45 distance guess** (ADR 0028).

## Revisit when

- **There are a few hundred real notes**: themes (clusters named by a model, with an
  eval) become worth building.
- **A native build happens anyway** (Apple Health): a Skia canvas could draw curves and
  pan/zoom, if the plain layout starts to feel cramped.

## Interview angle

> **"How would you visualise a knowledge graph for a user?"**

Start from the question they're asking. A global graph looks impressive and answers
nothing: past a few dozen nodes it's a hairball. An ego-graph (one entity and its
neighbours, re-centred on tap) stays legible at any scale, and walking it is how people
explore. Keep the edges honest: only draw links the system actually has, with the
relation in words; cap and interleave neighbours so one dense relation doesn't crowd
out the rest. And build it from existing queries: a view over data you already have is
cheap to add and cheap to throw away.
