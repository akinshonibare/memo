# 0017 — Length-dependent chunking: short captures are one chunk, long ones split on structure

**Status:** active — since [#31](https://github.com/akinshonibare/memo/issues/31) (2026-09-28)
**Date:** 2026-09-27
**Closes:** Q4, [#29](https://github.com/akinshonibare/memo/issues/29)

## Problem

Retrieval returns chunks, and a citation points at a chunk, so the chunk is the unit of both
recall and provenance. Chunk size trades two things: smaller chunks are more *precise* (the
vector describes one idea, the citation points at one passage) and larger chunks are more
*coherent* (the LLM gets enough context to answer from). Every RAG tutorial then picks a
number — 512 tokens, 10% overlap — and moves on.

memo's wrinkle: a typed voice note is often 40 words. Chunking that at all is wrong — a
40-word capture split into two 20-word chunks produces two vectors that each describe half a
thought, and a citation that points at half a sentence. The strategy has to depend on length,
which is a more interesting answer than a number.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Fixed token windows with overlap** (the default everywhere) | Trivial. Predictable chunk count. Overlap stops a sentence from being cut in half at a boundary. | Ignores structure: splits mid-paragraph, mid-list, mid-thought. Over-fragments short text — the exact failure above. Needs a tokenizer or an approximation. |
| **Sentence / paragraph boundaries** | Chunks are things a human would call a passage. Citations read well. A paragraph is usually one idea, which is what the vector wants to describe. | Paragraphs vary wildly in length — a 2,000-word paragraph is one chunk. Needs a length cap and a fallback, so it's never *only* this. |
| **One chunk per capture** | For most of memo's captures this is simply correct: the capture *is* the passage. No fragmentation, citation = capture, no overlap duplication. | A long dictated ramble becomes one 3,000-token vector describing eight topics at once. Recall collapses on exactly the captures with the most in them. |
| **Semantic chunking** (split where embedding similarity between adjacent sentences drops) | Splits on topic shifts rather than punctuation. The most principled answer. | An embedding call per sentence *before* the embedding call per chunk. Non-deterministic across model versions, which makes chunk boundaries — and therefore citations — unstable over time. Overkill until evals say structure-based chunking is the bottleneck. |

## Decision

**Length-dependent, in two regimes:**

- **≤ ~200 tokens** (roughly ≤ 800 characters — most captures): **exactly one chunk.** The
  capture is the passage.
- **Longer:** split on **paragraph boundaries** (blank lines), falling back to **sentence
  boundaries** for a paragraph that alone exceeds the target, packing units into chunks of
  **~400 tokens** with **~15% overlap** carried from the end of the previous chunk. A final
  fragment under ~50 tokens is merged into its predecessor rather than emitted alone.

Tokens are approximated as characters ÷ 4 — no tokenizer dependency, and a ±20% sizing error
is irrelevant to retrieval quality.

Chunks are stored with `capture_id`, an ordinal, and their character offsets into the source
text, so a citation can highlight the passage rather than just name the capture.

## What we're accepting

**Overlap duplicates text.** A sentence that lives in two chunks can be cited twice; the
retrieval layer de-duplicates by `capture_id` before fusion so the same capture doesn't take
two of the top-k slots.

**Chunk boundaries change if the strategy changes.** A citation stored against a chunk id
survives (the row is never deleted on re-chunk — new chunks are written, old ones marked
superseded), but the *text* an old citation points at may no longer be the passage a new
retrieval would choose.

**The approximation is crude.** Code blocks, URLs, and non-Latin scripts tokenise nothing
like 4 characters per token. For a personal English-language journal this is noise; it is
named so nobody trusts the number in a different corpus.

**Paragraph structure is only as good as the input.** Dictated audio has no paragraphs — a
long transcript is one paragraph and goes straight to the sentence fallback, which is fine,
but it means transcripts chunk differently from typed text and the eval set should contain
both.

## Revisit when

- `recall@10` in [#33](https://github.com/akinshonibare/memo/issues/33) is low and the
  failures cluster on long captures — that is the signal to try semantic chunking or a
  smaller target.
- Citations are frequently judged "technically right, wrong passage" — that points at
  overlap or the target size, not the model.
- Long captures become common (V1.5 streaming voice). Transcripts will need sentence
  segmentation from the STT provider rather than punctuation heuristics.

## Interview angle

> **"How do you chunk, and why not just use 512 tokens with overlap?"**

Because the right answer depends on what's being chunked, and a personal journal is mostly
short. A 40-word note split into two 20-word chunks gives you two vectors that each describe
half a thought and a citation that points at half a sentence — the fixed window is actively
wrong there, so short captures are one chunk, full stop. Above that, I split on structure
first — paragraphs, then sentences — because a paragraph is usually one idea and that's what
I want a vector to describe, with a token cap so a wall of text doesn't become one giant
embedding, and modest overlap so a boundary doesn't cut a sentence in half. The follow-up
I'd expect is semantic chunking; my answer is that it's the principled version of the same
idea, costs an embedding call per sentence before you've embedded anything, and makes
citation boundaries drift when the embedding model changes — so it's what I'd reach for
when the eval says structure-based chunking is the bottleneck, not before.
