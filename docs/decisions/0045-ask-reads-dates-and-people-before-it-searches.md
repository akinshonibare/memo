# 0045 — Ask reads dates and people from the records before it searches

**Status:** active
**Date:** 2026-09-29

Closes three gaps the handoff carried: date-range questions (ADR 0021's "not done"),
"when did I last see X" answered from the person (ADR 0029's open item), and Deepgram
keyterms from people's names (ADR 0013's reason for choosing Deepgram).

## Problem

Ask is retrieval-augmented generation: embed the question, find the nearest notes, hand
them to the model. That works when the question shares meaning with the answer. Two of
the most common questions to a personal journal don't:

- **"What's on this week?"** names no thing, only days. The dentist note written three
  weeks ago says "dentist moved to the 2nd", and nothing in "what's on this week" is near
  it, lexically or in vector space. Whether it came back was luck.
- **"When did I last see Tunde?"** matches every note that mentions Tunde, and the
  best-matching one is whichever *talks about seeing Tunde* most, not the latest. The
  answer is a `max()` over dated contacts that memo already stores (ADR 0029). Asking
  retrieval to find it is asking a similarity search to do arithmetic.

Both are the standard RAG failure: **structured questions sent down an unstructured
path.** The fix is also standard: answer the structured part from the structured store,
and let retrieval do what it's good at.

## Options

**Reading the time out of a question**

| Option | Buys | Costs |
| --- | --- | --- |
| **Self-query**: a model turns the question into a query plus a metadata filter (LangChain's `SelfQueryRetriever`, LlamaIndex auto-retrieval): the industry default | Any phrasing: "the week after my birthday", "over Christmas" | A model call on every question, on the time-to-first-token budget (~300-600 ms); non-deterministic; needs its own eval before it can be trusted |
| **Deterministic parser** over a closed set of phrases | Zero latency, free, unit-testable, the same answer every time | Misses phrasings outside the set; those fall back to plain retrieval |
| **Tool use**: the answer model calls `items_between(start, end)` | The model decides when and what to look up | Breaks the invariant that citations are chosen *before* generation (ADR 0005's design, the `retrieval` event before the first token); a second round trip |

**"When did I last…"**

| Option | Buys | Costs |
| --- | --- | --- |
| Retrieval only, better-worded chunks (the status quo: mention chunks carry "spoke on the phone on 2026-09-20") | Nothing new | The latest contact still has to out-rank every other mention |
| **Look up the named person's last recorded contact** and pin that note | Exact, and the same number the People screen shows | Has to spot names in the question |

## Decision

**Before searching, Ask reads what it can know exactly, pins it to the top of the notes
list, and tells the model what it pinned. Then retrieval runs as before, inside the
question's days when it names some.**

1. **Days** (`services/timewindow.py`): a deterministic parser for today, tonight,
   tomorrow, yesterday, this/next/last week, weekend and month, "the past week", weekday
   names (and "wed", "fri"…), "6 October", "October 6", "in March", "3 days ago", "a week
   ago", "the past 10 days". Past tense points a bare "Friday" or "March" backwards.
   Several phrases give the span covering them all.
2. **Dated items**: todos due, events on, and todos ticked off in those days, read from
   the table the Planner reads, earliest first, at most 15. Each is represented by its
   note's opening chunk, so the model reads the note and everything taken from it, and
   the citation opens the note.
3. **People named in the question** (`people.named_in`): a full name or alias anywhere
   in the question, or a given name alone. A full match outranks a given-name match with
   the same given name ("Tunde Ade" doesn't bring Tunde Bakare). For each, the latest
   in-touch mention is pinned, and the prompt gets a line: `Tunde Bakare: last in touch
   Sun 20 Sep 2026, spoke on the phone [1]`, or `no contact recorded; mentioned in 4
   notes`. At most three people.
4. **Retrieval inside the window**, then outside it to fill up to `top_k`. Inside a
   window the candidates are a few dozen chunks, so the vector branch computes exact
   distances over a `MATERIALIZED` CTE rather than walking the HNSW index, because
   post-filtered HNSW returns almost nothing for a selective filter (the filtered-ANN
   problem ADR 0010 named). The query is embedded once for both passes.
5. **The prompt** is `answer-v2.4`: "The question is about: Monday 28 September 2026 to
   Sunday 4 October 2026", and the people lines. The model is told to say which days it
   answered for when the phrase could have meant others.

**Alongside, because they are the same problem:**

- **Keyterms.** Every transcription (voice notes, hold-to-talk and live voice in Ask) sends
  up to 50 names to Deepgram as `keyterm=` parameters: each person's name and
  proper-noun aliases, most recently mentioned first, kinship words ("Mum") left out.
  Only on Nova-3/Flux, which support it.
- **Stale item chunks.** Items embedded before their dates were part of their chunk text
  (ADR 0021's open backfill) are rewritten by an hourly sweep, 64 in one embedding call.
  It also catches any future change to `derivation_text`. With nothing stale it costs a
  query.

## What we're accepting

- **Phrasings outside the set get no window.** "Over Christmas", "the week after next",
  "the 2nd", "the last few days" are known misses (`evals/timewindow`, 41/45). They fall
  back to plain retrieval, which is what Ask did before, so a miss is never worse than
  the old behaviour. `ask.time_unparsed` logs the time-like word in each missed question
  (the word only, never the question) so real misses can become cases.
- **A wrong window is worse than none**, since the wrong items get pinned. The eval
  allows zero; it caught one on its first run ("what have I got on in November" read as
  last November, because "have I" counted as past tense).
- **"Next Friday" is ambiguous** in English. memo reads it as the first Friday after
  today, and the prompt asks the model to say which date it used.
- **Note time is not event time.** A note written on Monday about the weekend is outside
  "last weekend". Dated items cover the planned case, and the fill-up retrieval usually
  catches the rest, but not always.
- **Name spotting errs towards matching.** "Will" the person matches "what will I…".
  The cost is one line in the prompt the model ignores; a missed name costs the answer.
- **Keyterms can bias transcription** towards a name that wasn't said. Deepgram's own
  guidance is 20-50 terms; memo sends at most 50, proper nouns only.

## Revisit when

- **`evals/timewindow` falls below 0.85 on real questions** (add them from
  `ask.time_unparsed`), or the misses cluster on phrasings a parser can't reasonably
  cover: move to self-query with the rewrite model, measured by this same eval.
- **Ask needs other structured facts** (habits, prayers, "how many times did I…"): the
  pinning step is the place, and at that point tool use may beat a growing list of
  special cases.
- **People go past ~50**: rank keyterms by recent *contact*, not recent mention.

## Interview angle

> **"How do you handle time-based questions in a RAG system?"**

Similarity search can't answer "what's on this week?" because the question shares no
meaning with the answer; the relevant rows are picked by a date, not by content. So
split the question: resolve the time part into a filter over structured metadata, and
use retrieval only for the content part. The textbook way is self-querying (a model
writes the filter); I used a deterministic parser first because it adds no latency and
can be tested exhaustively, with an eval that measures coverage and a logged-miss
channel that tells me when to upgrade. And when you filter a vector search tightly,
don't post-filter an ANN index: for a small candidate set, compute exact distances.

> **"Your RAG system gets 'when did I last…' wrong. Why?"**

Because "last" is an aggregate, `max(date)`, and retrieval returns the most *similar*
passage, not the most recent. If the fact is stored structurally, look it up and hand
it to the model with its source, then retrieve for context.
