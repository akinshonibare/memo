# Open questions — decisions deliberately not made yet

These are real forks. Each one gets the full comparison treatment (see the comparison rule
in [../CLAUDE.md](../CLAUDE.md)) at the phase where it becomes load-bearing, and then
becomes an ADR. Deciding early would mean deciding without information.

---

### Q1 — LLM provider and model for extraction
**Resolved 2026-09-27 → [ADR 0014](decisions/0014-claude-opus-5-for-extraction.md)**

`claude-opus-5`, quality-first, because NFR4 makes extraction correctness application
correctness and the whole monthly cost difference at ~20 captures/day is a few dollars
(Opus $5/$25 vs Haiku $1/$5 per MTok, verified at decision time). The "small model for the
easy 80%" idea was not rejected — it was ruled *early*: it needs the golden set to exist so
"easy" is a number. Revisit trigger: Haiku within 2 points of Opus on todo recall.

---

### Q2 — Structured output mechanism
**Resolved 2026-09-27 → [ADR 0015](decisions/0015-structured-outputs-and-terminal-validation.md)**

Native structured outputs (`output_config.format`) from the same Pydantic model that
validates — not forced tool use, which the newest Claude models now reject with a 400. The
useful distinction the ADR adds: the API guarantees *syntactic* validity, so the only
failures left are *semantic* (a date that doesn't parse, an empty title), and those are
deterministic. Policy: one repair with the validation error appended, then terminal
`failed` with `failure_reason=schema_validation` and the raw output stored — visible and
retryable, never dropped.

---

### Q3 — Embedding model
**Resolved 2026-09-27 → [ADR 0016](decisions/0016-voyage-4-embeddings.md)**

`voyage-4`, 1024 dimensions, `input_type` document/query, one model for chunk rows and
derivation rows alike. The self-hosted option lost for the same reason 0013 declined to
self-host STT: no homelab, and the text is on Railway's disks regardless. The sleeper issue
survives as a hard requirement: `embedding_model` and `embedding_dim` on every row from the
first migration that has one.

---

### Q4 — Chunking strategy
**Resolved 2026-09-27 → [ADR 0017](decisions/0017-length-dependent-chunking.md)**

Length-dependent, as the wrinkle predicted: ≤ ~200 tokens is exactly one chunk; longer
captures split on paragraphs, then sentences, into ~400-token chunks with ~15% overlap.
Tokens approximated as chars/4. Chunks carry character offsets so a citation can point at a
passage, not just a capture. Semantic chunking is deferred until `recall@10` says structure
isn't enough.

---

### Q5 — Primary key strategy
**Resolved 2026-08-18 → [ADR 0011](decisions/0011-uuidv7-primary-keys.md)**

`uuid` v7, column type `uuid`, generated in Python rather than by a column default. Postgres
bumped 17 → 18 at the same time — not for native `uuidv7()` (the app generates the value) but
because a major upgrade costs a deleted empty volume today and a scheduled outage later.

The framing in the original question was half wrong and worth keeping as a note: **the index
locality argument is real but does not apply at memo's scale.** Random-key write amplification
— scattered page dirtying, ~50% fill, full-page WAL writes after each checkpoint — needs an
index that doesn't fit in memory before it is measurable. At ~30k chunks it never will be. The
decision was made on guessability under the [0010](decisions/0010-multi-tenant-schema-single-user-product.md)
tenant boundary and on who is permitted to mint an identity.

**Explicitly not collapsed:** the primary key and `client_token` stay separate columns. RLS
does not conceal unique-index violations, so a client that mints the PK can probe for rows it
cannot read.

---

### Q6 — Job status: Redis, Postgres, or both
**Resolved 2026-09-27 → [ADR 0012](decisions/0012-job-status-on-the-capture-row.md)**

The leaning held, argued properly: `captures.status` (+ `failure_reason`, `attempt_count`)
is the truth because a state the user must act on days later needs the durability of the
thing it describes, and a cache is *designed* to forget. Redis holds only arq's own
bookkeeping. The stated downside: two stores can disagree after a crash, so Postgres is
authoritative and a sweep re-enqueues stale `pending` rows. A `jobs` table is the revisit,
not the default.

---

### Q7 — Auth (or none) for V1
**Resolved 2026-09-27 → [ADR 0018](decisions/0018-static-bearer-token-for-v1.md)**

The constraint fired: Q8 gave the API a public URL, so "no auth" became a security decision.
Resolved with the lightest defensible option — a static bearer token in a dependency
gating every route but `/health` — and a distinction worth keeping: **gating** (can this
request reach the API) is what the token does; **identity** (which user) still comes from
`get_current_user` per [ADR 0010](decisions/0010-multi-tenant-schema-single-user-product.md),
which is where real auth plugs in at V2. Tailnet-only lost because it is the homelab plan in
a different hat.

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
**Resolved 2026-09-27 → [ADR 0013](decisions/0013-hosted-stt-not-self-hosted-whisper.md)**,
which supersedes [ADR 0006](decisions/0006-self-hosted-faster-whisper.md)

Hosted — Deepgram Nova-3 (prerecorded API, $0.0043/min verified at decision time; under
$4/month at memo's volume) behind a `Transcriber` protocol. On the axis this question
named, hosted wins both sides: better on proper nouns, and zero hours of model serving
taken from the learning budget. Deepgram over the cheaper vendors for keyterm boosting,
which is the one feature that speaks directly to the proper-noun problem. 0006 is marked
superseded and left unedited — it was *overtaken*, not wrong.

---

### Q10 — Is memo multi-user, or is multi-user only a design exercise?
**Resolved 2026-08-18 → [ADR 0010](decisions/0010-multi-tenant-schema-single-user-product.md)**

Both, deliberately: the **schema** is multi-tenant from migration 1, the **product** stays
single-user with auth deferred to Q7/[#41](https://github.com/akinshonibare/memo/issues/41).
`user_id` is `NOT NULL` on every user-owned table and denormalised onto `derivations` and
`chunks`; composite indexes lead with it; the current user comes from a dependency, never
from a request body.

The deciding argument was **not** migration cost — adding the column later is metadata-only
and the backfill at V1 scale is seconds. It was that retrofitting a tenant predicate onto an
existing retrieval path fails silently, and that Phase 4's `recall@k` should measure the
filtered query shape that will actually be true.

**Consequences:** [00-brief.md](00-brief.md) amended (2026-08-18) so the schema/product split
is stated rather than looking like drift. [ADR 0002](decisions/0002-pgvector-not-a-vector-database.md)'s
revisit trigger sharpened — filtered ANN is now the default query shape, not an edge case.
Q5 (primary keys) resolved the same day in its favour — see [ADR 0011](decisions/0011-uuidv7-primary-keys.md).

**Still open, tracked in 0010 rather than here:** RLS is the enforcement mechanism but lands
at [#6](https://github.com/akinshonibare/memo/issues/6), not [#7](https://github.com/akinshonibare/memo/issues/7)
— policies are cheap to retrofit, the column and index shapes are not.

---

### Q11 — Habit tracking, without a streak
**Resolved 2026-09-29 → [ADR 0043](decisions/0043-habits-noticed-not-logged.md)**: habits are
noticed in notes by a reader with its own eval, suggested once they repeat, counted by
rhythm (days a week, the last time, eight weeks), with time with family and Bible reading
computed from People and Faith. No Apple Health. The original framing, kept for the record:

The industry default for habits *is* the streak (Streaks, Duolingo, Habitica): a chain
that a missed day breaks. memo has refused to keep score twice ([ADR 0029](decisions/0029-people-last-contact-and-reach-out.md):
a person is a suggestion, not a debt; [ADR 0041](decisions/0041-easter-eggs-are-rare-and-earned.md):
no streaks). So the real question is what a habit looks like when missing a day costs
nothing. Worth weighing when it becomes load-bearing:

- **Streaks.** The default, and the thing an interviewer will name. Rejecting it needs a
  stated reason, not a vibe.
- **Rhythm, not chains:** "4 of the last 7 days", "most weeks this month". Shows frequency
  without anything to break.
- **Last done, like last contact:** "last run: 3 days ago", and a gentle nudge when it's
  been a while. The People feature (ADR 0029) already works this way, and could be the
  template.
- **How a habit gets logged:** a tap, or said in a note ("went for a run") and picked up
  by the reader, which is memo's usual way in. Probably both, as with Faith.
- **Apple Health** (steps, workouts) would be the first integration that reads the
  phone's data into memo. Akin's rule so far is memo → phone only, so this needs his
  explicit yes before it's proposed as a default.
- **Overlap with Faith:** prayer and Bible reading are obvious first habits. They must be
  one record shown in two places, not two counts that disagree.

### Q12 — Should memo write mood to Apple Health's State of Mind?

Raised with [ADR 0058](decisions/0058-mood-in-within.md). Health's State of Mind is meant
to be self-reported, so memo's guesses don't belong there; your own word for a day might.
Writing needs HealthKit's write permission (`NSHealthUpdateUsageDescription`), which is a
native build, and Akin's rule is that memo writes to the phone only by a settings switch
(Calendar, Reminders). Decide when someone asks to see memo's mood in Health, not before.

