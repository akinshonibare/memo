# 0016 — `voyage-4` at 1024 dimensions for every embedded row

**Status:** provisional — becomes `active` when [#31](https://github.com/akinshonibare/memo/issues/31) lands
**Date:** 2026-09-27
**Closes:** Q3, [#28](https://github.com/akinshonibare/memo/issues/28)

## Problem

Every chunk and every derivation gets a vector. The choice of model fixes three things at
once: the *dimensionality* (which is the `vector(N)` column type, the HNSW index size, and the
per-query cost), the *quality on short informal text* (most published benchmarks score long,
clean documents — a journal is neither), and the *migration cost*, which is the sleeper:
changing model means re-embedding the entire corpus, because vectors from two models do not
live in the same space.

That last one is why this decision has a hard requirement independent of the pick: the model
name and dimensionality are recorded on **every row**, from the first migration.

## Options

Specs verified 2026-09-27 against the provider's docs.

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Self-hosted `sentence-transformers` / BGE on the worker** | No vendor, no key, no per-token cost. Strong open models exist. Consistent with the privacy instinct 0006 had. | A model to serve on a CPU container — the same argument [0013](0013-hosted-stt-not-self-hosted-whisper.md) declined. Slower ingestion. And the privacy instinct already lost at Q8: the text is on Railway's disks regardless. |
| **OpenAI `text-embedding-3-small/large`** | The industry default; the model most tutorials and most interviewers assume. Cheap, well documented. | Adds an OpenAI account to a stack that has Anthropic for generation. Voyage's current generation benchmarks ahead of it on retrieval. |
| **Voyage `voyage-4-lite`** | Cheapest Voyage tier, same 1024-dim default, 32K context. | Marginally lower quality than `voyage-4` for a cost saving that is invisible under the free tier. |
| **Voyage `voyage-4`** | General-purpose, 32K context, 1024-dim default with Matryoshka 256/512/2048, explicit `input_type` for query vs document (asymmetric embedding, which helps short queries against longer chunks). Anthropic's recommended embedding partner, so one *relationship* even if two keys. **First 200M tokens free** — memo's entire corpus for years. | Hosted: a Voyage outage stalls chunking (not capture; the durability boundary is before any of this). Lock-in is real because of the migration cost above. |
| **Voyage `voyage-4-large`** | Best quality in the family; 2048-dim option. | Bigger vectors for no measured benefit at 30k rows. Would be the answer if `recall@10` in [#33](https://github.com/akinshonibare/memo/issues/33) came back low. |

## Decision

**`voyage-4`, 1024 dimensions, `input_type="document"` for chunks and derivations and
`input_type="query"` at ask time. One model for both row types.**

Dual-granularity embedding ([0005](0005-hybrid-retrieval-with-rrf.md), curriculum Phase 4)
means chunk rows *and* derivation rows — two kinds of row, not two models. One model keeps
the vector space, the column type, and the index single.

1024 over 512 because at 30k rows the index fits in memory either way and halving the
dimension would be optimising a cost that doesn't exist. Over 2048 because there is no
evidence it helps on this corpus and it doubles everything.

## What we're accepting

**Hosted dependency on the ingestion path.** Capture is unaffected; the chunk+embed stage
retries with backoff and the capture sits at `embedding` until Voyage is back.

**Re-embedding is a full-corpus job.** Every row carries `embedding_model` and
`embedding_dim`, so a migration is a query for rows on the old model and a job — tractable,
but a job. A dimension change is additionally a new column, because `vector(1024)` is a
type.

**No local tokenizer.** Chunk sizing uses a chars/4 approximation
([0017](0017-length-dependent-chunking.md)) rather than Voyage's tokenizer, so a chunk can
land ~20% off its target token count. Fine for retrieval; not fine if we ever bill by token.

**Asymmetric `input_type` is a contract.** A query embedded as a document, or vice versa,
silently degrades recall with no error. The embeddings service has two functions with
different names, not one function with a flag.

## Revisit when

- `recall@10` on the retrieval eval is low *and* chunking has been ruled out — that is the
  trigger for `voyage-4-large`, or for testing an OpenAI model side by side.
- Voyage's free tier ends or pricing changes materially.
- A dimension reduction is ever worth it — which means the `chunks` table is large enough
  that the HNSW index no longer fits in memory, and [0002](0002-pgvector-not-a-vector-database.md)'s
  revisit triggers are firing at the same time.

## Interview angle

> **"How did you pick an embedding model, and what's the thing people get wrong?"**

The thing people get wrong is treating it as a swap. Two models don't share a vector space,
so changing model means re-embedding every row you have — it is a migration, not a config
change, and it's un-retrofittable in one specific way: if you didn't record which model
produced each vector, you can't even tell which rows need redoing. So the first decision is
"the model name goes on every row", and the model choice is second. On the choice itself I'd
name the industry default — OpenAI's `text-embedding-3` — then say why I deviated: the current
Voyage generation benchmarks ahead on retrieval, offers asymmetric query/document embedding
which helps when queries are short and chunks aren't, and its free tier covers a personal
corpus for years. Dimensionality is the axis to be quantitative about: 1024 because at tens
of thousands of rows the index is in memory regardless, so I'm not paying for the extra
dimensions and I'm not giving up quality for a saving I'd never measure.
