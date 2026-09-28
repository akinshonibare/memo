# Decision records

**Nothing here is set in stone.** These are provisional by default — written down so that
when we change our minds we know *what we were trading away*, not so that we're bound to
them. We build step by step; a decision only becomes real at the phase where code depends
on it.

The point is not the decision — it's the **rejected options**. A decision with no
documented alternatives is indistinguishable from an accident.

## Status vocabulary

| Status | Means |
| --- | --- |
| `provisional` | Written down, not yet load-bearing. Change freely — just update the file. |
| `active` | Code depends on it. Changing it costs work; say what work. |
| `under revision` | A premise broke. Don't cite it until it's replaced. |
| `superseded by NNNN` | Replaced. Kept, never deleted — the reversal *is* the lesson. |
| `load-bearing` | Reversing it means rewriting the app. Rare, and marked as such. |

Promote `provisional` → `active` when you write the first line of code that assumes it.
That moment is worth noticing.

## How to change a decision

Don't edit the reasoning to match the new answer — that erases the learning. Instead:

1. Set the old ADR's status to `superseded by NNNN`.
2. Write the new one, and give it a **"What changed"** section: which premise stopped being
   true, and whether the original reasoning was *wrong* or just *overtaken*.

Being able to say "this was right, then the world moved" versus "I misjudged this" is
the whole point of writing them down. Q8 already produced one of these on day one.

## Format

```markdown
# NNNN — <decision, as a claim>

**Status:** provisional | active | under revision | superseded by NNNN | load-bearing
**Date:** YYYY-MM-DD

## Problem          What we actually needed. Not the tool. The problem.
## Options          Table: option / what it buys / what it costs.
## Decision         One sentence.
## What we're accepting   The downside, plainly. Mandatory. No downside = not understood.
## Revisit when     A concrete, observable trigger.
## Interview angle  The question this maps to, and the 30-second answer.
```

## Index

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-python-not-rails.md) | Python, not Rails or Node | provisional — but unlikely to move |
| [0002](0002-pgvector-not-a-vector-database.md) | Postgres + pgvector, not a dedicated vector DB | provisional |
| [0003](0003-fastapi-not-django.md) | FastAPI, not Django or Flask | provisional |
| [0004](0004-arq-not-celery.md) | arq, not Celery | provisional — weakest of the set |
| [0005](0005-hybrid-retrieval-with-rrf.md) | Hybrid retrieval fused with RRF, not pure vector | provisional |
| [0006](0006-self-hosted-faster-whisper.md) | Self-hosted faster-whisper | superseded by 0013 |
| [0007](0007-expo-managed-not-native-swift.md) | Expo managed workflow, not native Swift | provisional |
| [0008](0008-one-primitive-many-views.md) | One capture primitive, derivations as views | load-bearing |
| [0009](0009-uv-and-src-layout.md) | uv, `src/` layout, Python 3.12 pinned | active |
| [0010](0010-multi-tenant-schema-single-user-product.md) | Multi-tenant schema under a single-user product | active |
| [0011](0011-uuidv7-primary-keys.md) | uuidv7 primary keys, generated in the application | active |
| [0012](0012-job-status-on-the-capture-row.md) | Job status on the capture row, Redis for arq only | active |
| [0013](0013-hosted-stt-not-self-hosted-whisper.md) | Hosted STT (Deepgram Nova-3), not self-hosted Whisper | active |
| [0014](0014-claude-opus-5-for-extraction.md) | `claude-opus-5` for extraction | active |
| [0015](0015-structured-outputs-and-terminal-validation.md) | Native structured outputs; validation terminal after one repair | active |
| [0016](0016-voyage-4-embeddings.md) | `voyage-4` at 1024 dims, model recorded on every row | provisional |
| [0017](0017-length-dependent-chunking.md) | Length-dependent chunking | provisional |
| [0018](0018-static-bearer-token-for-v1.md) | Static bearer token gates V1; identity from a dependency | active |

Only **0008** is genuinely hard to reverse. **0010** is the next-hardest — not because the
DDL is expensive, but because reversing it means editing the retrieval path, where mistakes
are silent. **0016** is third: changing the embedding model is a full-corpus re-embed, which
is why the model name is on every row. Everything else is a swap with a known cost, and each
ADR names that cost. Decisions we haven't made yet live in
[../open-questions.md](../open-questions.md).
