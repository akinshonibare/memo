# Reciprocal rank fusion, and why weighted score fusion needs tuning that RRF doesn't

> **DRAFT written by Claude for issue #32.** Rewrite in your own words on the learning
> pass.

## In one sentence

Vector and keyword search produce scores on incomparable scales, so combine them by
*rank* — `score = Σ 1/(60 + rank)` across retrievers — and nothing corpus-specific ever
needs tuning.

## What problem does it solve / what did people do before it

A journal needs both retrievers. Embeddings find paraphrase and mood ("when was I
anxious?" matches "could not focus, kept refreshing email") and are terrible at rare
tokens: a name like *Tunde* gets averaged into a dense vector with everything around it.
Full-text search is the mirror image — exact on names, dates and jargon, blind to
synonyms. So you run both and merge.

The obvious merge is a weighted sum of scores, and it is a trap. A cosine distance lives
in [0, 2] with a distribution that depends on the embedding model; `ts_rank_cd` is
unbounded and depends on document lengths and term frequencies in *this* corpus.
Any weight that balances them is tuned to one corpus at one size, and it goes stale as
the corpus grows — a weight that works at 1k chunks is wrong at 100k, and nobody notices
because nothing errors.

RRF throws the scores away and keeps only each retriever's *ordering*. A document at
rank 1 in one list and rank 3 in the other scores `1/61 + 1/63`; one that only the
vector branch found at rank 2 scores `1/62`. Documents found by both rise; documents
found strongly by one still surface; no normalisation, no weights. The constant 60 damps
the top ranks so a runaway rank-1 in one list can't dominate — and it is the one thing
you *can't* reason about very well, which is the honest cost.

memo does one more thing after fusion: it collapses to one chunk per capture, because
overlap chunks and derivation chunks would otherwise let a single capture fill several
of the k slots.

## The thing I got wrong first

Wanting to "just weight the better retriever higher." RRF is not the optimal fusion —
it discards magnitude, so a spectacularly good vector match and a merely-good one at the
same rank look identical — but it is *robust with zero tuning*, and robust beats optimal
until an eval proves otherwise. The eval's signature for "RRF is now the bottleneck" is
high recall@10 with poor top-3 ordering; the fix at that point is a cross-encoder
reranker, not a weight.

## How I'd explain it to someone in an interview (30 seconds, out loud)

Two retrievers with opposite blind spots — vectors for meaning, full-text for names and
rare tokens — so I run both. Their scores aren't comparable: cosine distance is bounded
and model-dependent, `ts_rank` is unbounded and corpus-dependent, so any weighted sum is
tuned to one corpus at one size and rots silently. Reciprocal rank fusion sidesteps that
by fusing on rank: each document scores the sum of one over sixty-plus-rank across the
lists. Nothing to normalise, nothing to retune. It's robust rather than optimal — it
throws away score magnitude — and the follow-up I'd volunteer is a cross-encoder
reranker on top, but only once the eval shows recall is fine and ordering isn't.
