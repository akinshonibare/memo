# 0069 — Lists you keep, filled from your notes; one name means one list

**Status:** active
**Date:** 2026-10-01
**Builds on:** [0008](0008-one-primitive-many-views.md) (one capture primitive; lists are
the second entity outside it, after people), [0029](0029-people-from-notes.md) (the
read/resolve split and the people table as the template), [0042](0042-faith.md) (a
reader that runs only when a note sounds like its subject), [0068](0068-highlights-are-what-you-marked-or-kept.md)
(the Highlights noise this takes some of)

## Problem

Akin: *"could we support creating lists, and the user adding to their lists when they
talk about it."* Groceries, books to read, films to watch, gifts, a trip. Today "we're
out of milk" becomes nothing, or an idea; "I should read Piranesi" becomes a Highlight
that isn't one. The want is a list that fills itself from speech and can be ticked.

Three things to decide: where list items live, how a spoken name finds its list, and
how much the reader is allowed to guess.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Reuse todos with a `list` tag | No schema change | Due dates and "done" don't fit a reading list; the Planner fills with onions; every todo eval case now has a tag to get right |
| **A `lists` table and a `list_item` derivation kind, linked by a composite foreign key** | A list outlives its notes (rename, archive, delete); items keep everything derivations have: a note to open, review status, delete cascade; isolation at the data layer twice (RLS on `lists`, the FK ties item and list to one user) | A migration (table, column, constraints); a second entity outside 0008's shape |
| Lists and items as their own tables | The cleanest entity model | A parallel review, undo and delete path to maintain, for rows that are items in every other way |
| Items on the extraction prompt (a fourth list) | One call per note | Every note pays for it, extraction's eval moves for a feature most notes don't use (0042 rejected this for the same reason), and a wrong list item fails extraction's gate |
| **A reader of its own behind a keyword gate** | Most notes cost nothing (the gate is a regex); its own prompt, model setting (`LISTS_MODEL`) and eval; measured and routed per 0065 | Another model call on the notes that pass the gate; a gate that misses a phrasing misses the item |

## Decision

**A `lists` table, items as `list_item` derivations with a foreign key to their list, a
dedicated reader behind a keyword gate, and a normalised key so one name means one
list.**

- **Model** (migration 0030): `lists(id, user_id, name, key, archived_at)`, RLS as for
  people, unique `(user_id, key)`. `derivations.list_id` with a composite foreign key
  `(list_id, user_id) -> lists (id, user_id) ON DELETE CASCADE`: an item can only sit on
  its own user's list, and goes with it. A CHECK keeps `list_id` on list items and only
  on them. `list_item` is not an item: the generic routes, the Planner, the phone and
  Ask's index never meet one; it lives under `/v1/lists`.
- **One name, one list** (`services/lists.list_key`): case, accents, "the", "my" and a
  trailing "list" don't make a new list, and the common ones have a canonical key:
  "shopping list", "grocery" and "groceries" are `groceries`; "to read", "books" are
  `books`; "to watch", "movies", "series" are `films`. A name with no match creates a
  list in the author's words ("Lagos trip"); an archived one named again comes back.
  A rename onto another list's name is refused (409): merging is a decision.
- **Reading** (`lists-v1`, `LISTS_MODEL`, Sonnet 5 to start): runs only when the note
  has a list-ish word (`might_list`), is told the author's existing lists so it files
  into them, and returns `list`, `title`, `quantity`, `confidence` per item. Past tense
  ("bought milk"), other people's shopping, negations and wishes without a title return
  nothing.
- **Filing** (`apply_list_items`) is deterministic and idempotent by replacement, like
  prayers. An item already open on the list under the same title is not added twice;
  ticked off, it can be needed again. Items are kept at once (`accepted`): a wrong one is
  a hold to remove, and removing memo's item marks it `rejected`, the label the eval
  wants; an item you typed is simply deleted.
- **The app**: a Lists place on Home; the lists with "3 to get · 2 done"; one list with
  open items on top, done below, a tick that moves an item down (and back), a field to
  add, a hold to remove, and a sheet to rename, archive or delete. An item from a note
  opens that note.
- **The gate** (`evals/lists`): item precision 0.90, recall 0.85, list accuracy 0.90, on
  24 starter cases (several items in one breath, quantities, named lists, titles inside
  sentences, and the negatives).

## What we measured

`lists-v1` on `claude-sonnet-5`, three live runs on 2026-10-01 through the Railway
worker environment, 24 cases each:

| Run | item precision (bar 0.90) | item recall (bar 0.85) | list accuracy (bar 0.90) |
| --- | --- | --- | --- |
| 1 (recorded for CI) | 1.00 | 1.00 | 1.00 |
| 2 | 1.00 | 1.00 | 0.96 |
| 3 | 1.00 | 1.00 | 0.96 |

Every item on every run was found and nothing was invented: four items in one
unpunctuated breath (l23), a title inside a sentence (l16), the author's own "Lagos
list" (l14), and all nine negatives (bought, finished, a todo, an event, someone else's
shopping, a negation, a day's account, a wish, "list" as a topic). The one wobble is
**l18, "remind me to buy stamps"**, which two runs put on a list other than groceries
(the label says groceries; the model made an errands-style list). That is a judgement
call the label may be wrong about, and it is the case to watch when real notes arrive.

## What we're accepting

- **A second entity outside 0008's shape.** People justified the first; lists justify
  the second for the same reason (a list outlives its notes). The third should make us
  ask whether the shape is wrong.
- **The gate is a regex.** "I want that cookbook" passes nothing and files nothing. The
  words are in one place and cheap to add; the eval has a case for each.
- **Canonical names are English and mine.** "Groceries" is the word; a person who says
  "shopping" sees "groceries". Rename fixes the label, not the key.
- **No merge.** Two lists that should be one stay two until a merge exists; the 409 says
  so rather than quietly joining them.
- **Ask doesn't know lists yet.** "What's on my shopping list?" is answered by the
  screen, not the model, until 0045's structured-lookup pattern is extended to lists.
- **Starter set.** The usual: 24 synthetic cases, measured on Sonnet; real removals and
  additions are the labels that make it honest.

## Revisit when

- An item removed or added by hand more than once a week: harvest, add cases, re-run.
- A phrasing people use misses the gate: add the word, add the case.
- Two lists keep being one: build merge (move items, keep the older list).
- Ask gets asked about lists: extend the pre-retrieval lookup (0045).
- The Flash-Lite move (0067) lands: run this eval there too; it is a cheap, lexical job.

## Interview angle

> **"Add a feature where speech updates structured state, say a shopping list. How do
> you keep it from making a mess?"**

Separate the guess from the decision. The model only answers one question per note,
"what does this put on a list, and which list did the author name", and only on notes a
cheap gate says are about lists, so most notes never pay. Everything that can be wrong
in an annoying way is deterministic: which list a name means (a normalised key with a
small canonical table, so spelling never forks a list), whether an item is already
there (no double milk), and what happens on a re-run (replace what nobody touched). The
rows are ordinary derivations, so review, undo and delete come free, and the table that
holds the lists is isolated by the same two mechanisms as everything else: row-level
security and a composite foreign key that ties an item to its own user's list. Then an
eval with hard bars, and every removal in the app is a labelled false positive for it.
