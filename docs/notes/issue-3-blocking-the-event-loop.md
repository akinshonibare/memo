# Blocking the event loop — what the experiment showed

Issue #3, Phase 0. Goal: feel the failure the async model prevents, before relying on it.

## Setup

One uvicorn worker (`--workers 1`, no `--reload`), a `/block` endpoint sleeping 5s, and
N concurrent clients. Four cells, varying two things: whether the *decorated* handler is
`async def` or `def`, and which sleep it calls.

## Results

| # | Handler | Sleep | 10 conc. | 50 conc. |
| --- | --- | --- | --- | --- |
| 1 | `async def` | `await asyncio.sleep(5)` | ~5s | ~5s |
| 2 | `async def` | `time.sleep(5)` | ~50s | ~250s |
| 3 | `def` | `time.sleep(5)` | ~5s * | ~10s * |
| 4 | `async def` | `await asyncio.to_thread(time.sleep, 5)` | ~5s | ~25s |

\* Cell 3 is predicted, not measured. The first implementation routed all four cells
through a single `async def` endpoint, so the `def` path never ran (see Mistakes below).

## The explanation

A uvicorn worker is one process running one event loop on one thread. The loop runs one
callback at a time to completion. There is **no preemption** — nothing takes the CPU back
from your code. The only way control returns to the loop is an `await` on something that
isn't ready yet.

So `time.sleep(5)` in an `async def` handler parks the thread. The loop is *inside* that
function and cannot accept a socket, resume a coroutine, or fire a timer. The other nine
requests aren't slow — they are **unstarted**, their bytes sitting unread in the kernel's
socket buffer. Hence 5s × N, and a staircase where request *n* completes at 5(n+1) seconds.

The failure is silent. No error, no warning, invisible in dev with one user. It appears in
production as a latency cliff under concurrency.

## Why cells 3 and 4 are not the fix they look like

FastAPI dispatches on the signature of the **decorated** function. A `def` handler is sent
to Starlette's threadpool; an `async def` handler runs on the loop. These are two different
pools with two different, fixed ceilings:

- `def` handler → anyio's default thread limiter, **40** tokens. `ceil(50/40) = 2` waves × 5s = 10s.
- `asyncio.to_thread` → the loop's default executor, `min(32, cpu_count + 4)` = **12** here
  (8 cores). `ceil(50/12) = 5` waves × 5s = 25s. Machine-specific — this number changes on Railway.

Neither is elastic. Past the ceiling you're back to a staircase with wider steps. Async
buys unbounded *waiting*; threads buy bounded *parallelism*. Different problems.

## The diagnostic

Under load, hit `/health`:

- **Blocked loop** (cell 2): `/health` hangs too. Everything is down.
- **Saturated pool** (cell 3): `/health` answers instantly while `/block` queues. One endpoint is down.

Same symptom at the endpoint, different blast radius, different fix. This is how to tell
the two apart in thirty seconds.

## What `time.sleep` is standing in for

The real versions of this in memo:

- `requests.get(...)` instead of an async client — every outbound LLM call.
- A sync DB driver, or SQLAlchemy issuing sync IO under an async session (Phase 1, `greenlet`).
- `open(path).read()` on an uploaded audio file.
- faster-whisper transcription. Note this one is *not* a GIL argument — CTranslate2 releases
  the GIL, so `to_thread` would give real parallelism. The reason it goes to an arq worker
  (Phase 2) is that a minute of CPU-saturating work has no business inside a request,
  competing for cores with the process that has to stay responsive.

