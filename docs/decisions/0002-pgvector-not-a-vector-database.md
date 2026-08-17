# 0002 — Postgres + pgvector, not a dedicated vector database

**Status:** accepted
**Date:** 2026-08-17

## Problem

We need approximate nearest-neighbour search over embedded chunks, filtered by metadata
(date range, derivation type, source capture), joined back to the rows that produced them,
and fused with a keyword search. All for a single user with — realistically — tens of
thousands of chunks in year one, maybe low hundreds of thousands ever.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Pinecone / Weaviate / Qdrant / Milvus** | Purpose-built ANN. Horizontal sharding, replication, sub-10ms at 10M+ vectors. Some ship hybrid search and reranking built in. Managed options remove index ops entirely. | **The dual-write problem.** Your source of truth is Postgres, your index is elsewhere; every write is now a distributed transaction you didn't want. Deletes and edits drift. Backfills become migrations you hand-roll. Metadata filters are a second, weaker query language. No joins — you get IDs back and re-query Postgres anyway. Plus another service, another bill, another failure mode. |
| **Chroma / LanceDB / FAISS (embedded)** | Trivial to start, no server. Great for a notebook. | Not a database. Weak or no concurrent-write story, weak durability, and you still need Postgres for the relational half — so you've *still* got two stores and the dual-write problem, just with worse operational properties. |
| **Postgres + pgvector** | One store. The embedding is a column on a row you already have, so consistency is free — the chunk and its vector are inserted in the same transaction. Metadata filters are `WHERE` clauses. Keyword search is `tsvector` in the same query plan. Joins to `captures`/`derivations` for citations cost nothing. One backup, one connection pool, one thing to operate. | ANN is not Postgres' specialty. HNSW index build is memory-hungry and slow to rebuild. Recall/latency tuning (`m`, `ef_construction`, `ef_search`) is on you. Filtered vector search can degrade — Postgres may pick the vector index and then filter, or filter then scan, and the planner doesn't always choose well. Above roughly 10M vectors on modest hardware you will feel it. |

## Decision

Postgres + pgvector, because at our scale the vector index is not the bottleneck — the LLM
call is — and transactional consistency between a capture, its derivations, and its chunks
is worth more than ANN throughput we will never use.

## What we're accepting

We accept worse raw ANN performance and manual index tuning. We accept that if memo ever
grew to millions of documents with heavy concurrent query load, we would have a migration
in front of us. We are betting — explicitly — that this is a single-user journal and that
day never comes.

We also accept that pgvector's filtered search can be planner-dependent. Mitigation: keep
the corpus small enough that a filtered exact scan is acceptable as a fallback, and measure
before assuming the index is helping.

## Revisit when

Any one of:
- p95 retrieval latency (vector stage only, excluding LLM) exceeds ~200ms, **and** profiling
  blames the index rather than the query.
- Chunk count passes ~2M.
- We need multi-tenant isolation with per-tenant indexes.

## Interview angle

> **"Do you need a vector database?"**

Usually not, and the reason is the dual-write problem, not performance. Your embeddings
describe rows that live in your primary database; putting the index in a second system
means every write is a two-phase problem, deletes drift, and you lose joins — so retrieval
returns IDs you have to re-fetch anyway. pgvector keeps the vector as a column on the row,
which makes consistency a transaction instead of a reconciliation job. The case *for* a
dedicated vector DB is scale (10M+ vectors, high concurrent QPS) and operational
specialisation, not correctness. Know the `HNSW` vs `IVFFlat` trade-off too: HNSW gives
better recall/latency and needs no training pass, IVFFlat builds faster and uses less
memory but needs representative data at build time and degrades as the data shifts.
