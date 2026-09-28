# Personal Memo App — Project Brief

> Captured 2026-08-17. This is the agreed starting point. Changes to it happen by
> amending this file with a dated note at the bottom, not by silently drifting.

## What this is

A personal capture-everything app: write, record voice (later: images), ask questions back
over everything, with todos/events/ideas auto-extracted. Over time it grows sections for
habits, faith, health, and relationships — becoming the single system Akin references
daily. Dual purpose: a real daily-use tool AND a vehicle for learning production-level
Python AI engineering (voice, LLM, RAG, vector DBs, streaming).

## Core architectural commitment (do not break this)

**One primitive, many views.**

- `captures` table: everything in (text/audio/image), timestamped. One write path. Never
  ask the user to categorize at capture time — dump first, extraction sorts it.
- `derivations` table: typed extractions pointing back at captures (todo, event, idea;
  later: habit observation, person mention, scripture ref, health note).
- `chunks` table: embedded text (pgvector column) for retrieval, linked to captures and
  derivations.
- Every future "feature" = a new derivation kind + a new query. **Never a new
  module/schema/app.**

## Stack (decided)

- **Backend:** Python only (no Rails). FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic
  settings, arq (background jobs, Redis), structlog.
- **DB:** Postgres + pgvector (NOT a separate vector DB). Hybrid retrieval: vector +
  full-text (tsvector) + metadata filters, fused with reciprocal rank fusion. Citations on
  every answer, always.
- **Embeddings:** embed both raw chunks (recall) and derivations (precision).
- **STT:** faster-whisper (self-hosted on homelab) for V1 batch; hosted streaming STT
  considered later.
- **Extraction:** LLM structured outputs → Pydantic-validated. Show extraction result
  immediately with one-tap correction. Never silent — silent extraction failure kills trust.
- **iOS:** Expo (managed workflow for V1), 3 screens: capture (text + hold-to-record),
  extraction review, ask. Native modules / custom dev client deferred to V2.
- **Deploy:** Docker + docker-compose locally; Railway for V1 (homelab later). CI on GitHub
  Actions: ruff, typecheck, tests, evals as a merge gate.

## Non-negotiable production practices (the learning layer)

1. **Eval harness from Phase 1:** ~40 hand-labeled real captures scoring extraction
   quality; runs in CI; PRs that degrade extraction fail.
2. **Transcription/extraction in arq workers, never in-request.** API returns capture ID
   immediately; job status modeling, retries, idempotency.
3. **Structured JSON logging** with request ID flowing API → worker. Log every LLM call:
   model, tokens, latency, cost.
4. **Explicit failure handling:** retries w/ backoff, visible dead-letter state ("capture
   failed — retry?"), never silent loss.
5. **Tests:** unit (extraction parsing vs saved LLM outputs), integration (real test DB via
   API), evals-as-tests.

## V1 scope — "Capture, extract, ask" (2–3 weeks of evenings)

1. Capture: text + record-and-upload voice (no streaming voice yet)
2. Extraction (todos, events, ideas) with immediate review/correction + eval set
3. Ask = full RAG: pgvector, hybrid retrieval, RRF, citations, streamed answers via SSE
   (streaming tokens IS in V1 — cheap, big UX win)
4. Morning view: nightly job — today's todos/events + one resurfaced old idea
5. All production scaffolding above

**Explicitly OUT of V1:** images, auth (single user, optionally Tailnet-only), habits/faith/
health, weekly review, calendar sync, push notifications, sharing.

**Ship test:** daily use for 2 weeks; "ask" answers a real question correctly ≥1×/day.

## Roadmap

- **V1.5:** streaming voice as its own milestone — WebSocket audio chunks from Expo, VAD,
  incremental STT, live transcript. (The genuinely hard engineering; deliberately sequenced
  after V1 ships so it's built against a working system.)
- **V2:** images (vision extraction), auth, Action Button capture via App Intents, EventKit
  calendar read, Sentry.
- **V3:** habits/faith/health as extraction types + scheduled queries; weekly review;
  resurfacing ("you wrote about this in March").
- **V4:** reconciliation (new capture updates an old idea — entity resolution + semantic
  dedup + decision policy; identified as the hardest problem in the system); push
  notifications with a brutal quality bar (hard-capped, specific, one-tap actionable,
  thumbs up/down feeding evals; nudge act-on rate ≥~30% is the health metric).
- **V5:** relationship layer — person entities, threads, state-based nudges (not
  cadence-based), pre-meeting briefs off calendar. All as views over the same capture store.

## Key product principles

- Read path exists from day one; capture is a cost, retrieval is the payoff.
- Push-side retrieval (morning view, resurfacing) is what creates attachment.
- Ration notifications brutally; one great nudge/week > seven mediocre.
- No guilt mechanics ever: no streaks, no overdue counts, opportunity framing only.
- Capture must be rewarding in itself: sharp structured summary shown instantly.

---

## Amendments

**2026-08-18 — the schema is multi-tenant; the product is not.**
Per [ADR 0010](decisions/0010-multi-tenant-schema-single-user-product.md), every user-owned
table carries `user_id` from the first migration and every retrieval query is filtered by it,
while V1 ships as a single-user app with auth still out of scope. This is not drift and it is
not a change of product scope — "single user, auth out of V1" above still stands. It is a
deliberate decision to pay for the tenant boundary while it costs a convention, rather than
later when it costs an edit to the retrieval path. If you are reading the schema and wondering
when memo became multi-user: it didn't.

**2026-09-27 — three lines in "Stack (decided)" are amended by later decisions.**
- *STT:* not `faster-whisper` on a homelab. There is no homelab (Q8, 2026-08-17); transcription
  is hosted at Deepgram per [ADR 0013](decisions/0013-hosted-stt-not-self-hosted-whisper.md),
  which supersedes 0006. The privacy rationale for self-hosting died with the topology.
- *Deploy:* Railway only, no "homelab later". Same source.
- *Auth:* still out of V1 as a *product* feature, but the API now has a public URL, so V1
  ships behind a static bearer token per [ADR 0018](decisions/0018-static-bearer-token-for-v1.md).
  That is gating, not identity; real auth remains a V2 item.
