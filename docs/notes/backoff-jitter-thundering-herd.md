# Exponential backoff with jitter, and failure classification

> **DRAFT written by Claude for issue #17.** Rewrite in your own words on the learning
> pass.

## In one sentence

Retry transient failures with delays that grow exponentially *and are randomised*, and
don't retry deterministic failures at all — the first half protects the provider, the
second half protects your bill.

## What problem does it solve / what did people do before it

A provider returns 503 to every worker at once. Fixed-delay retries mean every worker
comes back at exactly the same moment, the provider falls over again, and the retry
schedule *is* the outage — the thundering herd. Exponential backoff spreads retries over
time (2s, 4s, 8s…), but every worker still hits the same instants. **Jitter** picks a
random delay in `[0, 2^attempt]` so a hundred workers land across a window instead of on
a point. "Full jitter" is the version with the best measured recovery time and the least
code: `uniform(0, min(cap, base * 2**(attempt-1)))`.

Classification is the other half. A 429 or a 5xx or a timeout is *transient*: the same
request later will probably work. A 400, a 401, or a response that doesn't validate is
*deterministic*: the same request later fails the same way, and each retry costs a
provider call. memo names these `RetryableError` and `TerminalError`, decides at the
boundary where the HTTP status is known, and the pipeline treats them differently — the
retryable one schedules a delayed re-run and bumps `attempt_count`; the terminal one goes
straight to `failed` with a reason the user can read.

The `attempt_count` cap turns a retryable failure into a terminal one after N tries, so
a capture that can *never* succeed (a provider that is down for a day) doesn't hold a
worker forever — it becomes a visible dead-letter the user can retry by hand.

## The thing I got wrong first

Treating "retry" as one thing. Retrying a schema-validation failure with backoff feels
diligent and is pure waste: the model will return the same shape again. The useful
question on every failure is *would doing this again produce a different result?* If
not, stop and surface it.

## How I'd explain it to someone in an interview (30 seconds, out loud)

Two rules. Classify first: transient failures — rate limits, server errors, timeouts —
retry; deterministic ones — bad input, auth, validation — don't, because you'd pay to
reach the same failure. Then for the retries, exponential backoff with full jitter: the
delay ceiling doubles each attempt, and the actual delay is uniform between zero and that
ceiling, so a fleet of workers that all failed on the same blip comes back spread across
a window rather than in a synchronised herd that recreates it. Cap the attempts, and make
the cap durable, so a capture that will never succeed becomes a visible failed state
rather than a job that retries until the heat death of the queue.
