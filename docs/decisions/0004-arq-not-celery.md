# 0004 — arq, not Celery

**Status:** provisional — **weakest decision in this set, flagged deliberately**
**Date:** 2026-08-17

## Problem

Transcription takes seconds to minutes. Extraction takes seconds and can fail. Neither may
happen inside the HTTP request. We need: durable enqueue, retries with backoff, a
queryable job status (so the app can show "transcribing…" / "failed — retry?"), scheduled
jobs (the nightly morning-view build), and idempotency so a retry doesn't double-write
derivations.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **FastAPI `BackgroundTasks`** | Zero infrastructure. | Not a queue. Runs in the same process; a deploy, crash, or OOM silently loses the job. No retries, no status, no visibility. Disqualified — but worth knowing *why*, because it's the tempting wrong answer. |
| **Celery** | The industry default. Enormous feature set: routing, chords/chains, rate limits, Flower for monitoring, broker choice, battle-tested at scale. The answer an interviewer expects. | Sync-first by design; asyncio support has been awkward and second-class for years. Heavy configuration surface. To call our async DB layer from a task you end up bridging loops or maintaining a parallel sync stack. Big dependency for a single-user app. |
| **Dramatiq** | Simpler and saner than Celery, good retry/middleware model, solid docs. | Also sync-first. Same async-bridging problem, smaller community, less interview currency. |
| **RQ** | Dead simple, Redis-only, very readable. | Sync-only, no async story at all, thin scheduling. |
| **arq** | asyncio-native — a task is a coroutine, so it shares the exact same `AsyncSession`, httpx client, and structlog setup as the API. Redis-only. Small enough (~1k lines) to read end to end, which is itself a learning asset. Built-in retries, job results with TTL, cron. | Small project, thin ecosystem, no monitoring UI, no complex workflow primitives (chords, chains, canvas). Release cadence has been intermittent — bus-factor risk is real. Fewer Stack Overflow answers when you're stuck at 11pm. |
| **Taskiq / SAQ** | Also asyncio-native, actively developed; SAQ is arq-like with a web UI, Taskiq is more pluggable. | Even smaller communities than arq. Less written about. |

## Decision

arq — because we are async end-to-end and arq lets a worker share the API's session
factory, HTTP client, and logging context with no bridging layer, and because reading its
entire source is a legitimate way to learn how a job queue actually works.

## What we're accepting

We accept a real bus-factor and ecosystem risk. arq is small and not aggressively
maintained; if we hit a bug we may be reading source and patching rather than filing and
waiting. We accept no monitoring UI — we'll be building job-status visibility ourselves,
which is arguably good (it's on the learning list) but is unpaid work Flower would have
given us.

We are **not** accepting a scaling limit — for one user, throughput is irrelevant.

To keep the exit cheap: **all job logic lives in plain async functions that take explicit
arguments and know nothing about arq.** The arq task is a three-line adapter. Swapping to
Taskiq or Celery then touches only the adapter layer.

## Revisit when

Any one of:
- We need a workflow primitive arq doesn't have (fan-out/fan-in with a join).
- We hit an arq bug with no maintainer response within two weeks.
- memo stops being single-user and job observability becomes an operational need.

## Interview angle

> **"How do you run background work in a Python service?"**

The default answer is Celery, and you should say so before you say anything else —
naming the industry default first is what shows you didn't pick from novelty. Then give the
reason to deviate: Celery is sync-first, and in an async codebase every task needs a bridge
back into your event loop to reuse the DB session and HTTP client. arq is asyncio-native so
the task *is* a coroutine sharing the app's resources.

The deeper question underneath is usually **idempotency**: what happens when a job runs
twice? Retries, at-least-once delivery, and a worker dying mid-task all guarantee it will.
The answer is a deterministic job key plus a uniqueness constraint at the database level —
e.g. derivations keyed on `(capture_id, extraction_version)` — so the second run is a
no-op conflict rather than a duplicate row. "We use retries" is half an answer; "we use
retries and the write is idempotent because of this constraint" is the whole one.
