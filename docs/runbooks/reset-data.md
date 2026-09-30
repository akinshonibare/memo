# Reset everyone's data, keep the accounts

A one-off: empty memo for every user before it is used for real, while everyone stays
signed in with their settings (ADR 0059). The command is `memo.admin.reset`, run from a
shell inside the `api` container, the only place with a route to production Postgres.

## When to use it, and when not

- **Use it** to clear test data out of production for everyone at once.
- **Not for one person.** Someone clearing their own memo uses Profile → *Delete all
  data* in the app (`DELETE /v1/me/data`). That keeps their account the same way,
  runs as them, and touches nobody else.
- **Not to delete accounts.** Accounts, sessions and push tokens are exactly what this
  keeps. Deleting an account is Profile → *Delete account*.

## What it keeps and what it wipes

| Kept | Wiped |
| --- | --- |
| `users`: every account and every setting on it (timezone, name, voice, auto-accept, Faith, translation, Habits switch, morning push time, Apple and email identity) | Notes and everything taken from them: `captures`, `derivations`, `chunks`, `people`, `habits`, `recordings` |
| `sessions`: every phone stays signed in | Ask: `conversations`, `messages`, `message_citations` |
| `push_tokens`: morning pushes keep reaching the same phones | Mornings: `morning_views`, `resurfacings`, `pushes` (with your thumbs up/down) |
| `alembic_version` | Ledgers: `job_runs`, `llm_calls` (the cost history goes too) |

Also:

- `users.bible_reading` (the passage you're reading now) is set to NULL for everyone:
  it describes where you were in memo, not how memo should behave.
- **Every object in the bucket** is deleted: recordings, photos, and any upload that
  never became a note. Railway Buckets have **no versioning**; this cannot be undone
  by any means, backups included.
- **arq's queue** in Redis is cleared: the `memo` sorted set and every `arq:job:*`,
  `arq:in-progress:*`, `arq:retry:*`, `arq:result:*` key. Rate-limit counters and the
  worker's health check stay.

A table that is in neither list makes the command refuse and name it, as does a
foreign key from a kept table into a wiped one (which `TRUNCATE ... CASCADE` would
follow). Classify the table in `src/memo/admin/reset.py`, deploy, then run it.

## Before you run it

1. **Pick a quiet moment.** Nobody recording, nobody mid-upload: a note saved just
   before the commit is wiped with the rest, and one saved just after can have its
   queued job cleared (the stale-note sweep re-queues it within about 25 minutes).
   A note sitting in a phone's offline outbox is fine; it arrives afterwards as a new
   note.
2. **Take a manual backup.** Railway → the `Postgres` service → *Backups* → take a
   backup now, and wait until it shows as complete. Railway also keeps weekly fulls and
   daily differentials (about four weeks; see `backups-and-restore.md`), but a manual
   one taken minutes before is the restore point you want. It does not cover the bucket.
3. Make sure the deploy you're about to use is the one with this command (it lives in
   the image, under `src/memo/admin/`).

## Run it

From your own terminal:

```bash
railway ssh --service api
```

In the container's shell (the image puts `/app/.venv/bin` first on `PATH`, so `python`
is the venv's; no `uv run`, and `WORKDIR` is already `/app`):

```bash
# 1. Check you're where you think you are. Prints "production".
python -c "from memo.config import get_settings; print(get_settings().app_env)"

# 2. Dry run: prints the plan (database host, user count, rows per table, objects in
#    the bucket, jobs in the queue) and changes nothing.
python -m memo.admin.reset --dry-run

# 3. The reset. Prints the same plan, then asks you to type the phrase it shows,
#    e.g. `reset 3 users`. Anything else stops with nothing changed.
python -m memo.admin.reset
```

The phrase has to be typed. It can't be piped in, passed as a flag or set in the
environment; with stdin not a terminal the command refuses. If that happens inside
`railway ssh`, you're not in an interactive shell: open one with no command after it.

What happens, in order:

1. **One transaction**: the checks again (a deploy may have migrated while you read),
   `TRUNCATE` of every wiped table `RESTART IDENTITY CASCADE`, and `bible_reading`
   cleared. It waits at most 10 seconds for the table locks; if the app is holding
   them, it rolls back, says so, and nothing changed. Run it again.
2. **The bucket**, listed and deleted a thousand keys at a time.
3. **The queue.**
4. **A summary**: every wiped table's count after (0), users before and after (the
   same), objects left in the bucket (0).

If step 2 or 3 fails, the database part is already committed. Run the command again:
its plan shows 0 rows and whatever is left in the bucket or the queue, and a second run
removes it.

A `morning_views` or `pushes` row can appear in the summary if the worker's morning
build or push ran in the seconds after the commit. That is today's empty morning being
built, not leftover data.

## Afterwards

- Open memo on a phone: it should be empty, still signed in, with the same settings.
- Phones remove memo's Calendar events, Reminders todos and event notifications on
  their next sync, which runs when the app comes to the front.
- Home-screen widgets show old content until they next refresh.

## Getting it back

The database: restore the manual backup from step 2 to a new service and cut over, as
in `backups-and-restore.md`. The bucket: no way back; restored notes will point at
recordings and photos that no longer exist (the transcript and description on each
note survive, which is the durable copy).
