# 0012 — Job status lives on the capture row, not in Redis

**Status:** provisional — becomes `active` when [#13](https://github.com/akinshonibare/memo/issues/13) lands
**Date:** 2026-09-27
**Closes:** Q6, [#11](https://github.com/akinshonibare/memo/issues/11)

## Problem

The app has to show "transcribing…" and "couldn't process this — retry?" — and the second one
has to still be true three days later when the user finally opens the app. Something has to
hold that state, and arq already holds *a* state: it keeps job results in Redis under a TTL.
The question is whether that is the state the product reads.

Two properties are needed and they pull in different directions: the worker needs a fast,
lossy place to coordinate (locks, in-flight markers, retry timers), and the user needs a
durable, queryable place that never forgets. One store cannot be both without lying about one
of them.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **arq's Redis result store only** | Nothing to build. `Job.status()` and `Job.result()` already exist, with retries and timers handled. | Results expire (`keep_result` defaults to an hour). Redis is configured as a cache, and a cache that restarts empty is *correct*, so "failed — retry?" silently becomes "no such job". NFR1's *visible, retryable state, queryable days later* is unrepresentable. No join to the capture without a second lookup. |
| **Status column on `captures`, Redis for arq only** | The status is a column on the row the UI is already reading, so `GET /v1/captures/{id}` is one query and the state is as durable as the capture itself. Backed up with everything else. `failure_reason` and `attempt_count` sit next to it. Partial index `WHERE status='failed'` makes the dead-letter view cheap. | Two places now describe the same job — Redis knows a task is queued, Postgres knows the capture is `pending` — and a crash between the two leaves them disagreeing. Every transition is an explicit `UPDATE` the code has to remember to make. |
| **A separate `jobs` table** (one row per attempt, capture points at latest) | Full attempt history, per-stage timing, and one place for every kind of job, not just capture processing. The shape most large queues (and Celery's result backend) converge on. | A join on the hot read path, a second identity to keep in sync, and a table that grows without bound. Answers questions V1 doesn't ask. |
| **Both, with Redis as the read cache** | Fast status polls without touching Postgres. | A cache in front of a column that changes maybe five times per capture. Invalidation bugs to solve a latency problem that doesn't exist at 20 captures/day. |

## Decision

**A `status` column on `captures` is the truth; Redis holds only arq's own bookkeeping and
nothing the product reads.** `failure_reason` and `attempt_count` live beside it, and the
transitions are enforced in one function (`memo.status`) rather than by scattered `UPDATE`s.

The deciding argument is the durability requirement, not convenience: a state the user must
be able to act on days later has to live in the store that has the same durability as the
thing it describes.

An idempotency ledger (`job_runs`, unique on `(capture_id, stage, version)`) rides alongside
in [#18](https://github.com/akinshonibare/memo/issues/18) — that is *not* job status, it is
the constraint that makes a duplicate run a no-op. The distinction matters: status is for the
user, the ledger is for the database.

## What we're accepting

**Two stores can disagree during a crash.** A worker that dies after Redis dequeues and
before Postgres is updated leaves a `pending` capture with no job. The mitigation is that
Postgres is authoritative and a periodic sweep re-enqueues anything `pending` older than a
threshold — cheap, but it is code we own.

**No attempt history.** `attempt_count` says how many times, not what happened each time.
Logs carry that; if per-attempt forensics ever matter, that is the moment for the `jobs`
table.

**A status column is a state machine written in a `varchar`.** Nothing in Postgres prevents
`ready → transcribing`. The single transition function is discipline, not enforcement — a
`CHECK` on allowed values catches typos, not illegal transitions.

## Revisit when

- The product needs to show *why* a previous attempt failed, not just that it did.
- A second job type appears that isn't attached to a capture (the nightly morning view is
  the first candidate — if it needs user-visible status, it gets the `jobs` table).
- The `pending`-sweep starts firing in production, which means the crash window is real
  rather than theoretical.

## Interview angle

> **"Where does job state live, and why not just use the queue's result backend?"**

Separate two consumers of the state. The worker needs a fast, lossy place to coordinate —
that's the queue's job and Redis is right for it. The user needs a durable, queryable answer
to "what happened to the thing I saved on Tuesday", and a cache with a TTL cannot give that
answer by construction: it is *designed* to forget. So the status the product reads is a
column on the row the product already reads, with the same durability and backups as the data
itself. The queue's own state is an implementation detail I never expose. The cost I'd name
unprompted: two stores can disagree after a crash, so one of them has to be authoritative and
a sweep has to reconcile — I pick the database, because a job that is lost is a product bug
and a job that runs twice is what idempotency is for.
