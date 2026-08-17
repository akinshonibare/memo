# Learning contract

## Why this repo exists (the second reason)

Akin is using memo to learn production Python + AI engineering, and to be able to *defend*
those choices in an interview. The app shipping is necessary but not sufficient — if memo
works and Akin can't explain why pgvector instead of Pinecone, the project failed at its
second job.

## What "done" means for the learning half

By the end of V1, Akin should be able to do all of the following **without notes, out loud**:

### Python / backend
- Explain what `async`/`await` actually does — event loop, coroutines, why a blocking call
  in an async handler poisons the whole process.
- Explain why SQLAlchemy 2.0 async needs `greenlet`, and what lazy-loading does under async.
- Explain the difference between a request-scoped session and a worker-scoped session, and
  what goes wrong when you share one.
- Explain Pydantic v2's role: it's a validation boundary, not a type annotation.
- Explain why migrations are code (Alembic) and what an irreversible migration costs.

### AI / RAG
- Explain what an embedding is, geometrically, and why cosine similarity fails on proper
  nouns and rare tokens.
- Explain chunking: why chunk at all, what chunk size trades off, why overlap exists.
- Explain HNSW vs IVFFlat, and what `ef_search` / `lists` actually tune.
- Explain reciprocal rank fusion in one sentence and say why it beats normalizing scores.
- Explain "structured outputs" — how a model is constrained to a schema, and what happens
  when it still fails.
- Explain what an eval is, why "it looked good in testing" is not one, and what precision/
  recall mean for extraction specifically.
- Explain hallucination mitigation via citations, and why a citation is a *retrieval*
  artifact not a *generation* artifact.

### Production practice
- Explain why transcription must not happen in the request, in terms of a specific failure.
- Explain idempotency for a job queue, and how you'd make "retry" safe.
- Explain what you log per LLM call and why cost-per-request is a first-class metric.
- Explain a dead-letter queue and what the user sees when a job lands in one.

## How we'll know it worked

Three checks, run periodically, not at the end:

1. **Cold quiz.** Claude asks one of the above out of nowhere. No notes, no scrollback.
   A wrong answer is data, not a failure — it becomes the next session's topic.
2. **The teach-back.** Akin writes the explanation into `docs/notes/` in his own words.
   If it can't be written in ≤10 lines, it isn't understood yet.
3. **The rebuild test.** Could Akin rebuild this component from scratch in a new repo
   without looking at memo? If no, the code was copied, not learned.

## Session shape

A good session looks like:

1. **Frame** — what problem are we solving today, and why now (5 min of talking, not code).
2. **Compare** — options on the table, trade-offs named, decision made, ADR written.
3. **Akin builds** — Claude is available for "why is this erroring", not "write it for me".
4. **Review** — Claude reads the code and names what's wrong *and the failure mode*.
5. **Note** — Akin writes the teach-back into `docs/notes/`.

Step 5 is the one that gets skipped. Don't skip step 5.

## Anti-goals

- **No copy-paste learning.** If Akin doesn't know what a line does, it doesn't go in.
- **No cargo cult.** "Everyone uses Celery" is not a reason. "Celery is sync-first and we're
  async end-to-end" is a reason.
- **No premature abstraction.** Write it concretely twice before extracting.
- **No mystery green.** A passing test you don't understand is worse than a failing one.
