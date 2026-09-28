# At-least-once delivery, and why "exactly once" is a marketing claim

> **DRAFT written by Claude for issue #18.** Rewrite in your own words on the learning
> pass. If you can't say the "interview" paragraph without looking, it isn't yours yet.

## In one sentence

A queue can promise a job runs *at least* once or *at most* once, never *exactly* once;
memo picks at-least-once and makes every job safe to run twice, with the database — not
the code — deciding which run counts.

## What problem does it solve / what did people do before it

A worker picks up `process_capture`, transcribes for forty seconds, and dies before it
acknowledges. What should the queue do? If it never redelivers, the capture is silently
lost (at-most-once). If it redelivers, transcription runs twice and you pay twice — and
if the job also wrote rows, you now have duplicates (at-least-once). "Exactly once" would
need the job's effects and the acknowledgement to be one atomic operation across two
systems, and they aren't: Redis and Postgres don't share a transaction.

So the honest contract is at-least-once plus **idempotent effects**: running the job
again produces no new effect. The naive way is check-then-insert in code — "does a row
exist? no? insert" — which is a race: two workers both check, both see nothing, both
insert. The correct way is a **unique constraint** at the point of effect, so the second
insert *fails* and the failure is the signal to skip.

In memo that's `job_runs`, unique on `(capture_id, stage, version)`. Before a stage runs,
the worker does one `INSERT ... ON CONFLICT DO UPDATE ... WHERE status = 'failed'`
`RETURNING id`. New row → run the stage. Existing failed row → reclaim it and run again.
Existing succeeded row → nothing comes back, skip. Two workers racing get exactly one row
between them, decided by Postgres, with no lock in application code.

The `version` in the key is what makes re-extraction a feature instead of a bug: a new
`extractor_version` is a different key, so the new extractor runs over old captures while
the old runs stay recorded.

## The thing I got wrong first

Thinking arq's deterministic `_job_id` was the idempotency mechanism. It isn't — it only
stops a *duplicate enqueue* while a job is queued or running. It says nothing about a
worker that crashed mid-stage, a retry that re-enters the pipeline, or a user tapping
"retry" tomorrow. Queue-level dedup is a courtesy; the ledger in the database is the
guarantee.

The second thing: reaching for `max_tries` on the queue as the retry cap. arq's counter
lives in Redis and resets with it; `captures.attempt_count` lives in Postgres and doesn't.
The cap that decides "give up and show the user" has to be the durable one (ADR 0012).

## How I'd explain it to someone in an interview (30 seconds, out loud)

Delivery is at-least-once, because exactly-once would need the queue and the database to
commit atomically and they can't. So every job is idempotent, and idempotency is
enforced by a unique constraint, not by a check in code — check-then-insert is a race,
a constraint isn't. Each stage of a capture writes a ledger row keyed on capture, stage
and version; the insert either wins and the stage runs, or conflicts and the stage is
skipped. A retry, a crashed worker, or a user tapping retry all go through the same door.
The version in the key is the bonus: bumping the extractor version is how you re-run
extraction over history without touching the rows the old version produced.
