# HNSW vs IVFFlat, and what `m`, `ef_construction`, `ef_search` control

> **DRAFT written by Claude for issue #30.** Rewrite in your own words on the learning
> pass.

## In one sentence

Both are approximate nearest-neighbour indexes that trade a little recall for a lot of
speed; HNSW is a navigable graph you tune with `ef_search` at query time, IVFFlat is a
set of clusters you tune with `probes`, and HNSW wins at memo's scale because it needs
no training pass and degrades gracefully as data changes.

## What problem does it solve / what did people do before it

Exact nearest-neighbour over 1024-dimensional vectors is a full scan: compare the query
to every row. Fine at 30k rows (milliseconds), unacceptable at 30M. An ANN index answers
"probably the nearest" much faster by not looking at everything.

**IVFFlat** k-means-clusters the vectors at build time (`lists` clusters) and at query
time searches only the `probes` nearest clusters. Cheap to build, small in memory — but
it needs representative data *before* you build it, and as the corpus drifts the
clusters stop representing it and recall quietly decays until you rebuild.

**HNSW** builds a layered graph: every vector is a node linked to `m` neighbours per
layer, with sparse upper layers for long jumps and a dense bottom layer for precision.
A query walks the graph greedily from the top. `ef_construction` is how wide the search
is *while building* (bigger = better graph, slower build, more memory);
`ef_search` is how wide it is *while querying* — the candidate list it keeps as it
walks. That is the recall-vs-latency dial: raise it and you visit more nodes, find
better neighbours, and pay more time per query. It needs no training pass, so it is
right from the first row and stays right as rows are added.

The memo-specific wrinkle (ADR 0010): every query carries `WHERE user_id = ?`. pgvector
walks the graph *then* filters, so with a selective filter and a small `ef_search` you
can get fewer than `k` results back. `search()` sets `hnsw.ef_search` per transaction
(100 instead of the default 40) to widen the walk; the eval in `evals/retrieval` is
where a filtered-recall problem would show up before production does.

## The thing I got wrong first

Treating the index parameters as something to tune up front. Until an eval says
retrieval recall is the problem, the defaults (`m=16`, `ef_construction=64`) are right,
and the only knob worth touching is `ef_search`, because it costs nothing to change.

## How I'd explain it to someone in an interview (30 seconds, out loud)

Two ANN families. IVFFlat clusters the space and probes a few clusters — cheap, but it
needs representative data at build time and drifts as the corpus changes. HNSW is a
multi-layer graph you walk greedily; `m` is the degree, `ef_construction` the build-time
beam width, `ef_search` the query-time beam width, and `ef_search` is the one dial you
actually turn: higher means better recall and slower queries. I picked HNSW because it
needs no training pass, so it's correct from row one, and because I can adjust recall
per query. The thing I'd add: with a tenant filter on every query, HNSW post-filters,
so I widen `ef_search` and measure filtered recall rather than trusting the unfiltered
number.
