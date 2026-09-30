# 0060 — Ideas are called Highlights; names memo meets in notes are titled

**Status:** active
**Date:** 2026-09-30
**Amends:** [0029](0029-people-last-contact-and-reach-out.md) (how a new person is named)

## Problem

Two things Akin noticed using memo:

1. *Rename ideas to highlights, that's more correct.* What the extractor keeps as an
   "idea" is rarely an idea in the startup sense; it's the thing in a note worth coming
   back to: a line from a sermon, what Kemi said about the flat, a book to read.
2. *For people captured, can their names be titled?* A voice note's transcript says
   "kemi adeyemi", and memo showed that on the People page as it was said.

## Options

**The rename**

| Option | Buys | Costs |
| --- | --- | --- |
| **Rename the label only** (screens, sheets, the widget, the review) | No migration, no API change, no prompt change, no eval re-run; the app built before it keeps working | The word `idea` stays in the schema, the API (`kind: "idea"`, `new_ideas`, `memo://ideas`) and the prompt |
| Rename the kind everywhere (`idea` → `highlight`) | One word end to end | A data migration over every derivation, the check constraint, every client's enum, the extraction prompt and its recorded eval, deep links on phones: a lot of risk for a word |
| Rename and redefine what the extractor keeps | "Highlight" as a new, broader concept | A prompt change with a real eval question in it; not what was asked |

**Titled names**

| Option | Buys | Costs |
| --- | --- | --- |
| Leave names as said | Nothing lost | "kemi adeyemi" on a page |
| Python's `str.title()` / Postgres `initcap` | One call | "Mcdonald", "Dj", "Tunde'S", "Ruud Van Nistelrooy" |
| **Word by word: only an all-lowercase word changes; hyphens and O'-names handled; particles inside a name stay small** | Right for the names memo actually meets; a name written with capitals on purpose is untouched | A small function to own, and a particle list that is English-and-Dutch-ish |

## Decision

**Label only: the app calls ideas Highlights everywhere a person reads the word, with a
sparkles icon for the bulb.** The kind stays `idea` in the database, the API, the deep
link and the prompt; `KIND_NAME` in the app maps it to "highlight".

**Names memo meets in a note are titled word by word (`services/people.titled`)** when
it creates a person or learns an alias from a note. A name you type or rename is kept
exactly as typed ("bell hooks" stays). Migration 0026 titles the names already stored.

## What we're accepting

- **Two words for one thing**, `idea` in code and "highlight" on screen, like `faith`
  and Within (ADR 0058). Anyone reading the API has to know the mapping.
- **Titling can be wrong**: "de la soul" becomes "De la Soul", "KEMI" stays shouted. A
  rename fixes it, and a renamed name is never touched again.
- **Migration 0026 can't be undone** and doesn't know who typed what: a name typed in
  lower case before today is titled too. Its downgrade changes nothing.

## Revisit when

- The API gets a second client or a public reader: rename the kind properly then, with a
  versioned field, rather than leave the mapping to be learned.
- Names from other languages come out wrong often enough to notice: widen the particle
  list, or ask the people reader to return the name as it should be written.

## Interview angle

> **"Product wants to rename a core concept. Do you migrate the data?"**

Usually not at first. Rename the presentation layer, keep the stored enum, and document
the mapping; a schema rename touches every client, every stored row and every prompt
for no change in behaviour. Do the full rename when the old word starts to cost
something: a public API, a new team reading the code, an ambiguity that causes bugs.

> **"Why not `title()`?"**

Because names aren't title case: "McKenzie", "O'Neil", "van Nistelrooy", "DJ". Only
change what is clearly unintentional (a word in all lower case, which is how
transcripts write), and leave anything with a capital in it alone.
