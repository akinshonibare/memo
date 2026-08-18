# 0005 — Hybrid retrieval fused with reciprocal rank fusion, not pure vector search

**Status:** provisional
**Date:** 2026-08-17

## Problem

"Ask" must answer questions over a personal journal. Personal journals are dense with
things embeddings are bad at: names ("what did I say about Tunde?"), rare tokens, dates,
project codenames, scripture references, one-off jargon. They're also full of things
keyword search is bad at: "when was I anxious about work?" — where the word "anxious" may
never appear.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Pure vector (cosine over embeddings)** | Semantic recall. Finds paraphrases, related concepts, mood. The thing embeddings are actually for. | Fails hard on exact terms. A proper noun gets averaged into a dense vector alongside everything else in the chunk; a rare name has weak representation in the embedding model's training. Ask about "Tunde" and you get chunks about *people generally*. This failure is invisible in demos and obvious in daily use. |
| **Pure keyword (Postgres `tsvector` / BM25)** | Exact terms, names, IDs, dates. Fast, cheap, explainable, no model dependency. | No semantics. Zero results for a query that uses different words than the document — which is most of how humans ask questions. |
| **Hybrid, weighted-score fusion** | Uses both. | You must combine a cosine distance (0–2, distribution depends on the embedding model) with a `ts_rank` (unbounded, corpus-dependent). Normalising these requires per-corpus tuning that drifts as the corpus grows. A weight that works at 1k chunks is wrong at 100k. |
| **Hybrid, reciprocal rank fusion (RRF)** | Uses both, and fuses on **rank** rather than score: `score(d) = Σ 1/(k + rank_i(d))` across retrievers, conventionally `k=60`. Scores never have to be comparable, so there's nothing corpus-specific to tune. Documents found by both retrievers rise; documents found strongly by one still surface. | Discards score magnitude — a runaway-best vector match is treated the same as a merely-good one at the same rank. Adds a `k` you can't easily reason about. Not *optimal*, just remarkably robust for zero tuning. |
| **+ cross-encoder reranker** (Cohere Rerank, `bge-reranker`) | Best quality by a wide margin. A cross-encoder reads query and document *together* instead of comparing two independently-computed vectors. | Adds a model call, latency (~100–300ms), and either a vendor dependency or a self-hosted model. Only worth it once you have evals proving retrieval is the bottleneck. |

## Decision

Hybrid vector + `tsvector`, fused with RRF (`k=60`), with metadata filters applied as
`WHERE` clauses in both branches. Reranking is deliberately deferred.

## What we're accepting

We accept losing score magnitude in fusion, and we accept that RRF is a robust default
rather than a tuned optimum. We accept that without a reranker, the top-3 ordering will
sometimes be wrong even when the right chunk is in the top-10 — which is survivable because
we're feeding ~8 chunks to the LLM, not 1.

We also accept the extra write cost: every chunk maintains both an embedding and a
`tsvector`, and both indexes must be updated on write.

## Revisit when

The eval set shows **recall@10 is high but answer quality is low** — that's the specific
signature of "retrieval finds it, ordering buries it", and it's the trigger for adding a
cross-encoder reranker. If recall@10 itself is low, reranking won't help and the problem is
chunking or the embedding model instead.

## Interview angle

> **"Why not just use cosine similarity?"**

Because embeddings blur exactly what a search needs to be sharp about: proper nouns, rare
tokens, identifiers, dates. A dense vector is a lossy average of a whole chunk's meaning,
and a name that appeared rarely in training has a weak, unstable representation. Keyword
search is exact where vectors are fuzzy, and useless where vectors are strong. So you run
both.

The follow-up is *how you combine them*, and that's where most answers fall down: cosine
distance and BM25 scores are not on comparable scales, so any weighted sum needs
per-corpus tuning that goes stale as the corpus grows. RRF sidesteps this by fusing on rank
— `Σ 1/(k + rank)` — so no normalisation is needed and there's nothing to retune. It's not
optimal, it's *robust*, and that distinction is the point. If asked what you'd add next:
a cross-encoder reranker, because it scores query and document jointly rather than
comparing two vectors computed in isolation — but only after evals show ordering, not
recall, is the bottleneck.
