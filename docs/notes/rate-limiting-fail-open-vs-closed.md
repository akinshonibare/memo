# Rate limiting: where the counter lives, and fail-open vs fail-closed

> **DRAFT written by Claude for issue #48.** Rewrite in your own words on the learning
> pass.

## In one sentence

A per-user limit has to be counted somewhere every API instance can see, so it lives
in Redis — and the real decision is what the limiter does when Redis is unreachable,
which memo answers differently for the two endpoints it guards.

## What problem does it solve / what did people do before it

An ask endpoint with no limit is an unbounded bill: each call is retrieval plus a
model call, and a bug in the client (or a loop in a script) can spend real money in
minutes. Capture ingestion is cheaper but not free — every capture is a transcription
and an extraction downstream.

The naive limiter is an in-memory counter. It works until there are two API instances,
at which point each has its own counter and the limit silently doubles. So the counter
lives in Redis, keyed by user and endpoint and fixed window: `INCR` the key, set its
expiry on first touch, compare with the limit. Fixed windows have a burst at the
boundary (twice the limit across two adjacent seconds); sliding windows fix that and
cost more; for a personal app the fixed window is the right amount of machinery.

The interesting decision — the one issue #48 says to make out loud — is **what to do
when Redis is down**, because it *will* be down and the limiter has to pick a side:

- **Ask fails closed.** A bill guard that admits everything when it cannot count is
  not a guard. A 429 for a few minutes during a Redis outage is annoying; an unbounded
  bill is not.
- **Capture fails open.** Durability outranks limiting: memo's one non-negotiable is
  that a capture accepted is a capture kept, and refusing captures for the app's own
  operational reasons is exactly the silent-loss shape NFR1 forbids. Admitting a burst
  during an outage is the cheaper failure.

Both policies are named at the call site (`limit_ask`, `limit_capture`), the response
carries `Retry-After` and `X-RateLimit-Remaining` so the client can behave, and the
outage path logs a warning either way.

## The thing I got wrong first

Wanting one policy. "Fail open" or "fail closed" sounds like a property of the
limiter; it is a property of the endpoint, decided by what the endpoint costs and what
the product promises.

## How I'd explain it to someone in an interview (30 seconds, out loud)

Count in Redis so the limit holds across instances — a fixed window is INCR plus an
expiry, and I'd only go sliding if boundary bursts mattered. Then decide per endpoint
what happens when Redis is unreachable, because it's a policy, not an implementation
detail: the expensive endpoint fails closed, since a guard that fails open isn't one;
the durability-critical endpoint fails open, since refusing writes for my own
operational reasons is the one failure the product forbids. Return `Retry-After` so the
client can back off instead of retrying blindly.
