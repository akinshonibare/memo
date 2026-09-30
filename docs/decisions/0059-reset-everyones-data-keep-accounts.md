# 0059 — Resetting everyone's data keeps accounts, and wipes by an explicit list

**Status:** active
**Date:** 2026-09-30

## Problem

Akin is about to start using memo for real, and production is full of test notes,
people, habits and pushes from building it. He wants all of it gone for every user,
while everyone stays signed in with their settings, so the first real note lands in an
empty memo. It's a one-off, run against production, where a mistake is unrecoverable
(the bucket has no versioning) and the database has no public proxy: the api
container is the only way in.

The hard part isn't deleting rows. It is being sure what "data" means today and
staying right when a table is added next month, and not signing everyone out by
accident.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| **A command in the image: `TRUNCATE` an explicit WIPE list `CASCADE` in one transaction, refuse on any table in neither list** | One statement, atomic; fast at any size; the classification is written down and tested against the schema at head; accounts and sessions untouched by construction | A table added later must be classified before the command will run; `CASCADE` can empty a table you didn't name, so it needs a foreign-key check; `TRUNCATE` takes `ACCESS EXCLUSIVE` locks on a live app |
| Drop and recreate the database, migrate to head | Nothing survives by accident; the most obviously "empty" | Loses accounts, sessions and push tokens: everyone is signed out, and a new Apple sign-in makes a new account with new ids. The opposite of what was asked |
| Each user runs Profile → *Delete all data* (`DELETE /v1/me/data`), or the operator loops over users calling the same `_purge` | Already exists, tested, runs as the user; keeps the account and sessions the same way | Can't be done for someone else through the API (the user comes from the token, never a parameter, and that stays true); a loop calling `_purge` directly is per-user `DELETE`s, not one transaction across users; `_purge` misses things a full reset should cover: `users.bible_reading`, bucket objects with no row (it deletes keys it reads from `recordings`), and queued jobs |
| A destructive admin HTTP endpoint, e.g. `POST /v1/admin/reset` | Callable from anywhere; no shell needed | A route that wipes every user is the biggest target the API would have, and it needs an admin role memo doesn't have (ADR 0010: every user is a tenant, none is special). Guarding it well costs more than the one-off it serves; guarding it badly is a breach |

## Decision

**`python -m memo.admin.reset`, run by the operator from `railway ssh --service api`,
as the owner role: it classifies every table into `KEEP` (users, sessions,
push_tokens, alembic_version) or `WIPE` (the other fourteen), prints a plan with
counts, requires a typed phrase naming the user count (`reset 3 users`), then in one
transaction `TRUNCATE`s the WIPE list `RESTART IDENTITY CASCADE` and clears
`users.bible_reading`; after the commit it deletes every object in the bucket and
clears arq's queue.**

The guards, each for a specific failure:

- **A table in neither list refuses.** Otherwise a new table is wiped or kept by
  whatever the default was, which nobody chose. A test fails the build when a
  migration adds a table the lists don't name.
- **A foreign key into a WIPE table from outside it refuses.** `TRUNCATE ... CASCADE`
  follows those silently; a key from `sessions` to `captures` would sign everyone out.
- **The checks run again inside the transaction**, and the user count must match the
  plan: a deploy can migrate, and someone can sign up, while the operator reads.
- **`lock_timeout = 10s`.** `TRUNCATE` queues behind open transactions and every query
  queues behind it; failing fast is better than freezing the live app.
- **The phrase is typed at a terminal**, never a flag or environment variable, and it
  contains the count: a remembered or scripted phrase stops matching when the plan
  changes.

Database first, then bucket, then queue, because the database is the record: after
the commit, whatever the later steps leave behind is an orphan a second run removes,
never a row pointing at a missing object. `DELETE /v1/me/data` goes the other way
(bucket first) only because it finds keys through the rows it is about to delete; this
lists the bucket directly. Clearing the queue after the commit also means the
stale-note sweep can't re-queue a note that is about to vanish, and a queued job for a
wiped note doesn't spin through its retries looking for it.

## How it differs from Delete all data

| | `DELETE /v1/me/data` | This reset |
| --- | --- | --- |
| Who | one user, as themselves | everyone, as the operator |
| Rows | `DELETE ... WHERE user_id` per table, cascades for the rest | `TRUNCATE` of the same tables plus `job_runs`, `messages`, `message_citations`, `resurfacings` by name (the per-user wipe reaches these through cascades) |
| Kept | account, sessions, push tokens | the same |
| `bible_reading` | kept | cleared |
| Bucket | keys of the user's `recordings` rows | every object, orphans included |
| Queue | untouched | cleared |

The two agree on what an account is. The per-user wipe keeping `bible_reading` is
arguably a gap in it, not a difference in intent; noted, not changed here.

## What we're accepting

- **The bucket delete is final.** No versioning, and database backups don't cover it.
  A database restored afterwards has notes whose recordings and photos are gone (their
  transcripts and descriptions survive).
- **Everything a user added by hand goes too**: people they named, habits they set up,
  reach-out rhythms, thumbs on pushes. "Keep settings" means the columns on `users`,
  nothing else.
- **The cost history (`llm_calls`) goes**, so spend before the reset can't be read
  back from memo; the provider dashboards still have it.
- **It takes a moment of exclusive locks** on fourteen tables while the app is live,
  bounded by the lock timeout.
- **A worker job mid-flight at the commit can write a row just after it** (today's
  empty morning view, a skipped push). The closing summary shows any such row.
- **One more place that knows the table list.** The test at head keeps it honest; it
  is still a list someone has to update.

## Revisit when

- It is needed a second time: at that point it isn't a one-off, and it wants a
  per-user form (for a support case) and a staging environment to rehearse in.
- memo has more than a handful of users, or real users other than Akin: a global reset
  stops being a thing anyone should be able to run from a shell, and the answer
  becomes per-user deletion with an audit trail.
- The bucket gets versioning or lifecycle rules: the "final" above changes, and the
  bucket step should respect a retention window instead.

## Interview angle

> **"How would you wipe production data safely?"**

Decide what "data" means as an explicit allowlist, and make anything the list doesn't
cover a hard stop, not a default. Do the database part in one transaction so it's all
or nothing, check that `TRUNCATE ... CASCADE` can't reach a table you meant to keep,
and set a lock timeout so you don't freeze the live app waiting for locks. Show a plan
with counts and make the operator type something tied to it. Take a backup first, and
know which parts the backup doesn't cover (object storage). Order the side effects so a
failure halfway leaves orphans a rerun cleans, rather than rows pointing at nothing.

> **"`TRUNCATE` or `DELETE`?"**

`TRUNCATE` for emptying a table: it doesn't scan rows, doesn't fire row triggers,
doesn't leave dead tuples for vacuum, and is transactional in Postgres. It takes an
`ACCESS EXCLUSIVE` lock and bypasses row-level security, so it's an owner's tool, not
something request-path code should ever do. `DELETE ... WHERE` is for a subset (one
user), where you need row-level rules and triggers to apply.
