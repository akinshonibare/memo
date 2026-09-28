# 0021 — Ask reads each retrieved note's derivations live, and knows today's date

**Status:** active
**Date:** 2026-09-28

## Problem

On the phone, Ask answered a dentist question with "next Tuesday" and a hedge, while
extraction had already resolved it to 6 October and the user had confirmed it. The
knowledge was in the database; the answer path couldn't see it. There were three causes,
and only the first was the one anyone suspected:

1. **The derivation chunk had no date.** Dual granularity (ADR 0017) already embeds each
   derivation as its own chunk, but `_derivation_text` wrote only title, summary, notes and
   location. `due` and `starts_at` were dropped.
2. **Derivation chunks are frozen at processing time.** They are written once by the
   pipeline. A later confirmation, edit, or `user_created` addition never reaches them, and
   a derivation rejected after processing kept answering questions.
3. **The prompt had no "today".** Notes carried their written date, but the model was never
   told the current date, so it could not turn "next Tuesday" into a date itself or say
   whether something was past.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Re-embed derivations on every change**: a job re-embeds a derivation's chunk on add/edit/confirm, and supersedes it on reject | Retrieval itself sees current state; a question phrased around a date can match the derivation directly. The industry-default answer ("keep the index in sync"). | An embedding call and a queued job per edit; eventually consistent (a question asked right after an edit sees the old text); every write path must enqueue it, which is the invalidation-at-call-sites problem again. |
| **Attach live derivations to retrieved notes**: after retrieval, read the current derivations for the retrieved captures and put them under each note in the prompt | Fresh by construction, the same principle as ADR 0020: one query, no sync. The model sees the resolved date *and* whether the user vouched for it. | Only helps when the note itself was retrieved. A question that shares no words or meaning with the note ("what's on 6 October?") still depends on the derivation chunk matching. |
| **Inject an agenda**: every prompt carries the user's upcoming confirmed todos and events | Answers "what's on this week" even when retrieval finds nothing. | Prompt grows with the user's calendar; irrelevant items in every answer invite the model to mention them; mixes two retrieval strategies without ranking between them. |
| **Tool use**: give the model a `list_events(from, to)` tool | The most general: date-range questions become exact queries. | A second model round trip before the first token, against the TTFT budget; tool-call failure modes; a bigger change than the problem. |

## Decision

**Attach each retrieved note's current, non-rejected derivations to it in the prompt, with
their resolved dates and review status, and tell the model today's date (`answer-v2`).**
Alongside that, two small fixes to the chunks themselves: derivation chunk text now
includes `due`/`starts_at`, and rejecting a derivation supersedes its chunk.

## What we're accepting

- **Date-shaped questions still lean on retrieval.** "What's on 6 October?" only works if
  some chunk matches. The dated derivation chunk now helps, but only for captures processed
  after this change. Existing derivation chunks keep their undated text until re-embedded,
  and edits or additions are still not re-embedded (option 1 is not done).
- **Un-rejecting doesn't restore the chunk.** A derivation rejected and later confirmed
  stays out of the index until its capture is reprocessed. The app offers no un-reject, so
  this is theoretical today.
- **More prompt per answer.** A few lines per retrieved note, bounded by `top_k` and by how
  many derivations one note produces.
- **The model is told to trust confirmed dates over the note's words.** That is correct
  when the user confirmed a wrong date, too; the answer is now as good as the review.

## Revisit when

- Evals or real use show date-range questions ("what's this week?") missing. That is the
  trigger for the agenda injection or a tool, measured against TTFT.
- Derivation edits become common enough that stale derivation chunks visibly hurt
  retrieval. Then do option 1, as a job enqueued from the derivation write paths.
- The embedding model changes (ADR 0016). The full re-embed is the moment to backfill dated
  derivation text for free.

## Interview angle

> **"Your RAG system extracted a structured fact, the user confirmed it, and the chatbot
> still answered from the raw text. Why, and how do you fix it?"**

Because the index is a copy, and copies go stale. We embedded the extraction once, at
processing time, without its date. Everything the user did afterwards (confirming,
editing, rejecting) happened in the database and never reached the vectors. There are two
ways out: keep the index in sync, which means every write path enqueues a re-embed and
you're eventually consistent, or stop trusting the index for anything that changes. I
retrieve with the index but read the structured facts live at answer time, joined by the
retrieved note's id, and hand them to the model labelled with their review status. The
index decides *which* notes are relevant; the database decides *what's true about them*.
The cost I'd name: questions that only match the structured fact, like a bare date, still
depend on the index, so that's where I'd look next.
