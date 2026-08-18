# Open questions — decisions deliberately not made yet

These are real forks. Each one gets the full comparison treatment (see the comparison rule
in [../CLAUDE.md](../CLAUDE.md)) at the phase where it becomes load-bearing, and then
becomes an ADR. Deciding early would mean deciding without information.

---

### Q1 — LLM provider and model for extraction
**Needed by:** Phase 3

Extraction is the correctness-critical path (see [ADR 0008](decisions/0008-one-primitive-many-views.md)
— extraction quality *is* the app's quality). Choice interacts with structured-output
support, latency, and per-capture cost.

Do **not** decide this from memory — model IDs, pricing, and structured-output support all
move. Research current specs at decision time.

Axes: structured-output/tool-calling reliability · cost per capture at ~20 captures/day ·
latency (this is a background job, so seconds are fine — don't over-optimise) · whether a
small local model is viable for the easy 80% with escalation for the rest.

---

### Q2 — Structured output mechanism
**Needed by:** Phase 3

Three approaches: native tool/function calling with a JSON Schema · a library like
`instructor` (schema + automatic retry-on-validation-failure) · raw prompt + parse +
hand-rolled repair.

The interesting question isn't which is easiest — it's **what you do when the model returns
something that doesn't validate**. Retry with the validation error appended? Repair
mechanically? Surface a "couldn't parse" state to the user? The brief says *never silent*,
which rules out the tempting option of dropping it.

---

### Q3 — Embedding model
**Needed by:** Phase 4

Hosted embedding API vs. self-hosted (e.g. a `sentence-transformers` / BGE-family model on
the homelab, consistent with [ADR 0006](decisions/0006-self-hosted-faster-whisper.md)'s
privacy reasoning).

Axes: dimensionality (directly drives index size and query cost) · quality on short
informal text, which is what a journal is — most benchmarks measure long clean documents ·
privacy · **migration cost, which is the sleeper issue**: changing embedding model means
re-embedding the entire corpus, so record the model and version on every chunk row from day
one. That's cheap now and impossible to retrofit.

---

### Q4 — Chunking strategy
**Needed by:** Phase 4

Fixed token windows with overlap · sentence/paragraph boundaries · one chunk per capture
(many captures are short!) · semantic chunking.

Note the memo-specific wrinkle: a typed voice note might be 40 words. Chunking that at all
is probably wrong. The strategy likely needs to be length-dependent, which is a more
interesting answer than picking a number.

---

### Q5 — Primary key strategy
**Needed by:** Phase 1

`bigserial` · `uuid4` · `uuidv7`. Real trade-off: random UUIDs destroy B-tree index
locality on insert; sequential integers leak volume and are guessable in URLs; uuidv7 is
time-ordered and gets most of both. Decide before the first migration — changing it later
is painful.

---

### Q6 — Job status: Redis, Postgres, or both
**Needed by:** Phase 2

arq keeps job results in Redis with a TTL. The app needs to show "transcribing…" and
"failed — retry?" — potentially days later. Redis is a cache; the UI's state is not
allowed to expire.

Leaning: status lives in Postgres on the capture row, Redis is arq's business only. Worth
arguing properly rather than assuming.

---

### Q7 — Auth (or none) for V1
**Needed by:** before anything is exposed to the internet

Brief says single-user, optionally Tailnet-only. Options: nothing + Tailscale-only binding ·
a static bearer token · real auth deferred to V2.

Constraint worth stating: the moment audio and journal content leave localhost, "no auth"
stops being a scoping decision and becomes a security one. Tailnet-only is defensible;
public-with-no-auth is not.

---

### Q8 — Deploy topology
**Resolved 2026-08-17:** no homelab. Everything runs on Railway. Single environment, no
tunnel, no split worker. This also removes the brief's "homelab later" line — amend
[00-brief.md](00-brief.md) if that stays true.

**Consequence:** it invalidates [ADR 0006](decisions/0006-self-hosted-faster-whisper.md),
whose whole argument was that audio never leaves the machine. Where transcription runs is
now genuinely open — see Q9.

---

### Q9 — Where transcription runs, now that there is no homelab
**Needed by:** Phase 2 · **replaces [ADR 0006](decisions/0006-self-hosted-faster-whisper.md)**

Three credible options: hosted STT API · `faster-whisper` on a Railway CPU container ·
`faster-whisper` on serverless GPU (Modal / Replicate / RunPod).

The decision axis has *changed shape*, and that's the interesting part. It was
privacy-vs-ops. Now that audio sits on a cloud provider's disks either way, privacy is no
longer decisive, and the real trade is **STT accuracy on proper nouns** (which feeds
retrieval quality — see [ADR 0005](decisions/0005-hybrid-retrieval-with-rrf.md)) against
**how much of the V1 learning budget goes to model-serving ops** instead of to extraction,
retrieval, and streaming.

Verify current pricing and model availability at decision time rather than from memory.

---

### Q10 — Is memo multi-user, or is multi-user only a design exercise?
**Needed by:** before auth or any schema work · **raised:** 2026-08-17 (interview session 01)

The brief scopes V1 as single-user with auth explicitly out. The system-design session is
being run against a multi-user product at ~10k DAU.

Both are coherent. Either the product changed and [00-brief.md](00-brief.md) needs an
amendment, or the *design* deliberately runs ahead of the *build* so the schema doesn't
paint us into a single-tenant corner. The second is a legitimate and common move — but it
has to be stated, or V1 quietly ships a schema that can't grow.

**Concrete thing it decides now:** whether `user_id` exists on `captures` from the first
migration. Adding it later means backfilling and re-indexing every table including the
vector index. Cheap now, expensive in three months — so this one shouldn't sit open long
even if auth itself stays deferred.

**Also puts pressure on** [ADR 0002](decisions/0002-pgvector-not-a-vector-database.md):
filtered vector search was an edge case with one tenant. Multi-tenant, every query is
filtered, and per-tenant partitioning is one of the stronger arguments for a dedicated
vector DB.
