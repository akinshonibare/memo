# 0066 — When a provider refuses our account, notes wait instead of failing

**Status:** active
**Date:** 2026-10-01
**Builds on:** [0012](0012-job-status-on-the-capture-row.md) (attempts and the stale sweep),
[0063](0063-daily-ai-limit.md) (waiting without spending an attempt)

## Problem

On 2026-09-30 memo's Anthropic balance ran out in the middle of an eval run. Anthropic's
answer was a plain `400 invalid_request_error`: "Your credit balance is too low…". Every
provider wrapper sent non-5xx statuses to `TerminalError`, so any note captured in that
window would have gone straight to **failed**, as if its own text were the problem. And
Ask and voice show `str(exc)` to the user, so someone asking a question would have been
shown Anthropic's billing page text.

Making it a `RetryableError` wouldn't fix it either. A note gets five attempts with
2/4/8/16-second backoff, sized for a blip, so it would have failed in about 30 seconds
instead of at once. A balance stays empty until the operator notices and tops up, which
can be hours.

The real distinction is **whose fault is it**. A blip is the provider's, and short. A bad
request is the note's, and permanent. An empty balance or a refused key is *our
account's*: every call fails, no note is to blame, and it ends when the operator acts.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Leave it terminal | Nothing to build | Every note in an outage fails; users retry by hand, one by one |
| Make it `RetryableError` | One line | Fails anyway after ~30s; burns attempts meant for blips |
| **A third kind, `ProviderUnavailable`, waited out without spending attempts** | Notes survive an outage of a day or more and finish by themselves after a top-up | A message match for Anthropic's credit error; a ceiling on the wait |
| Pause the whole queue on the first refusal (a circuit breaker) | One failed call per outage instead of one per note per wait | Shared state across workers, a reset path, and a way to get it wrong that stops every user at once; worth it at volume, not at ~20 notes a day |

## Decision

**`ProviderUnavailable`, a subclass of `RetryableError`, for 401/402/403 from any provider
and for Anthropic's credit-balance 400.** The main pipeline lets the claim go without
counting an attempt (the same move as the daily limit), and the job comes back after
`provider_wait`: about a minute, doubling to an hour, half of it jittered.

Being a subclass is what keeps it small. Every place that already treats a
`RetryableError` as "not an attempt, try later" (optional stages, the people, habits,
mood and Lately sweeps, re-embeds) is already right, so only the one place that counts
attempts needed new code.

People see `ProviderUnavailable.for_users` ("memo's AI is unavailable right now. Your
notes still save, and they'll be read once it's back."), never the provider's text. The
operator sees `stage.provider_unavailable` at error level in the worker logs.

## What we're accepting

- **The credit check is a string match.** Anthropic sends no distinct error type for an
  empty balance. If the wording changes, this falls back to terminal, which is the old
  behaviour, not a new failure. A test pins the real message.
- **The wait has a ceiling.** arq stops a job after `ARQ_MAX_TRIES` (50). The waits cover
  at least 22 hours, about a day and a half on average. On the last try the note fails
  visibly with "the AI provider was unavailable for too long", and the app's retry
  brings it back. A longer outage means pressing retry, not lost notes.
- **Up to an hour's lag after a top-up.** A note late in its wait comes back on its own
  schedule.
- **A 401/403 from a misconfigured key now waits instead of failing.** That's right for an
  outage, but a deploy with a wrong key looks like "notes are slow" rather than "notes
  failed". The error-level log line is the signal, and with no alerting (by design,
  CLAUDE.md) someone has to look.

## Revisit when

- Volume makes one wasted call per note per wait matter: add the circuit breaker.
- An outage outlasts the ceiling in practice: raise `ARQ_MAX_TRIES`, or let the stale
  sweep requeue notes that are mid-pipeline with no job behind them.
- Anthropic adds a billing error type: match on it instead of the message.

## Interview angle

> **"How do you classify failures in a background pipeline?"**

By whose fault it is, because that decides what to do. Transient provider trouble gets
bounded retries with jittered backoff. A bad input fails at once and visibly, because
retrying spends money for the same answer. The case people miss is the third one: the
provider refusing *your account* (out of credit, a revoked key). Treat it as a bad input
and you dead-letter every job in the outage. Treat it as transient and you burn the retry
budget in seconds. The fix is to wait it out without spending attempts, with a ceiling
that ends in a visible failure rather than a silent drop. And the provider's own error
text, billing pages and all, goes to the operator's logs, never to the user.
