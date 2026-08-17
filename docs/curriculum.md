# Curriculum — concepts mapped onto build phases

The build order is chosen so that each phase forces one hard concept, in an order where
each one makes the next one make sense. Don't reorder for convenience — the sequencing is
the pedagogy.

Legend: **[C]** concept to understand before writing code · **[B]** thing to build ·
**[Q]** cold-quiz candidate

---

## Phase 0 — Foundations (evening 1–2)

Nothing AI here. This is the floor everything else stands on.

- **[C]** The event loop. Coroutines, `await`, why a coroutine is not a thread, what
  "blocking the loop" concretely does to concurrent requests. **[Q]**
- **[C]** Pydantic v2 as a *boundary*, not a type hint. Validation, coercion,
  `model_validate` vs constructor, settings from environment.
- **[C]** Dependency injection in FastAPI: what `Depends` actually does, request scope.
- **[B]** `docker-compose` with Postgres (pgvector image) + Redis. Health-check endpoint.
- **[B]** Pydantic settings loaded from env. One config object, no `os.getenv` scattered.

**Deliberate failure to experience:** put a `time.sleep(5)` in a handler, fire 10 concurrent
requests, watch them serialise. Then swap for `asyncio.sleep` and watch them not. This is
the single most useful five minutes in async Python.

---

## Phase 1 — The write path (evening 3–5)

- **[C]** SQLAlchemy 2.0 async: `AsyncEngine`, `async_sessionmaker`, why `greenlet` is a
  dependency, what lazy-loading does under async and why it explodes. **[Q]**
- **[C]** Session lifecycle: one session per request, never a global. What happens when two
  coroutines share a session.
- **[C]** Alembic: migrations as code, autogenerate's blind spots (it won't see enum
  changes, index options, or server defaults reliably), why you read every generated
  migration before applying it.
- **[B]** `captures`, `derivations`, `chunks` tables per [ADR 0008](decisions/0008-one-primitive-many-views.md).
- **[B]** `POST /captures` — writes and returns an ID immediately. No processing.
- **[B]** structlog with a request ID bound to context.

**Design question to answer yourself before coding:** what's the primary key strategy —
`bigserial`, `uuid4`, or `uuidv7`? There's a real answer involving index locality and
whether IDs leak information. Work it out, write it in `notes/`.

---

## Phase 2 — Background work (evening 6–8)

- **[C]** At-least-once delivery. Why "exactly once" is a lie and what you do instead. **[Q]**
- **[C]** Idempotency: deterministic job keys, uniqueness constraints as the enforcement
  point, why application-level "check then insert" is a race.
- **[C]** Retries with exponential backoff + jitter. Why jitter (thundering herd).
- **[C]** Dead-letter state as a *product* concept, not just an ops one — the user sees
  "capture failed — retry?".
- **[B]** arq worker sharing the API's session factory and logging config.
- **[B]** Job status modelled in the database (`pending`/`running`/`failed`/`done`), not
  just in Redis — because Redis is a cache and the user's UI is not allowed to lose state.
- **[B]** faster-whisper transcription job.

**Read this:** arq's source. It's ~1k lines. Reading a job queue end to end once means you
will never again treat one as magic.

---

## Phase 3 — Extraction (evening 9–12)

- **[C]** Structured outputs: how a model is constrained to a schema (tool/function calling
  vs. constrained decoding vs. "please return JSON" + parse), and the failure modes of
  each. **[Q]**
- **[C]** Why you validate the model's output with the *same* Pydantic model you'd use for
  an HTTP body — and what you do when validation fails (retry with the error? repair?
  surface to user?).
- **[C]** Prompt versioning. Why the prompt is code, belongs in git, and every stored
  derivation records which version produced it.
- **[C]** Evals: precision vs recall for extraction specifically. Missing a todo (recall)
  and inventing one (precision) are *not* equally bad here — decide which you optimise for
  and why.
- **[B]** Extraction job → typed derivations.
- **[B]** ~40 hand-labelled real captures. Golden set. Committed.
- **[B]** Eval runner producing per-kind precision/recall. Wired into CI as a gate.
- **[B]** Cost/latency/token logging per LLM call.

**This is the phase people skip.** The eval set is boring to build and is the difference
between an app you trust and a demo. Build it before the extractor is good, so you can
watch the number move.

---

## Phase 4 — Retrieval (evening 13–17)

- **[C]** What an embedding *is*: a learned map into a space where distance means
  similarity. Why that's lossy, and what it's lossy about. **[Q]**
- **[C]** Chunking: why chunk, what size trades off (retrieval precision vs context
  coherence), why overlap exists, why chunking on structure beats chunking on token count.
- **[C]** ANN indexes: HNSW vs IVFFlat, what `m` / `ef_construction` / `ef_search` and
  `lists` / `probes` actually control, and the recall-vs-latency dial. **[Q]**
- **[C]** Postgres full-text: `tsvector`, `tsquery`, `ts_rank`, GIN indexes, why a
  generated column beats a trigger.
- **[C]** RRF — see [ADR 0005](decisions/0005-hybrid-retrieval-with-rrf.md). **[Q]**
- **[C]** Dual-granularity embedding: chunks for recall, derivations for precision. Why
  both, and what each fails at alone.
- **[C]** Citations as retrieval artifacts — the chunk ID is known before generation, so a
  citation cannot be hallucinated if you construct it right.
- **[B]** Chunking + embedding job.
- **[B]** Hybrid retrieval query with RRF.
- **[B]** Retrieval evals: recall@k over hand-written question→expected-chunk pairs.

**Do retrieval evals before you do generation.** If retrieval is broken, no amount of prompt
work fixes the answer, and you'll waste a week tuning the wrong thing.

---

## Phase 5 — Generation + streaming (evening 18–20)

- **[C]** SSE vs WebSocket vs long-poll: what each costs, why SSE is right for one-way
  token streaming. **[Q]**
- **[C]** Streaming through the whole stack: provider stream → FastAPI generator → Expo.
  Where backpressure and client disconnect are handled (and what happens to your LLM bill
  if they aren't).
- **[C]** Grounding: how you make "answer only from these chunks" actually hold, and how
  you detect when it didn't.
- **[B]** `GET /ask` streaming SSE with citations.
- **[B]** Answer-quality evals (small; faithfulness to retrieved context).

---

## Phase 6 — The read path (evening 21+)

- **[C]** Scheduled jobs, timezones, and why "nightly" is harder than it sounds.
- **[B]** Nightly morning-view build: today's todos/events + one resurfaced idea.
- **[B]** Resurfacing selection — this is a ranking problem, treat it as one.

---

## Deferred, but know the names

You'll be asked about these even though they're not in V1. Be able to say what problem each
solves and why you didn't need it yet:

- Cross-encoder reranking · query rewriting / HyDE · multi-query retrieval ·
  contextual chunk headers · semantic caching · guardrails / output filtering ·
  agentic retrieval loops · GraphRAG · fine-tuning vs RAG (and why RAG almost always wins
  for personal, changing data) · entity resolution and semantic dedup (the V4 problem,
  and genuinely the hardest one in the system).
