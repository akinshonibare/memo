# Backups, and a restore you have actually run

NFR1 is "zero loss of accepted captures". A backup nobody has restored is a belief
(issue #47). This runbook makes it a number.

## What Railway retains (verified 2026-09-27 against Railway's docs)

- **Postgres**: pgBackRest takes a full backup weekly and a differential daily; the
  last four fulls are kept, so roughly **four weeks** of restore window. With
  **point-in-time recovery** turned on, every WAL segment is archived to a bucket as it
  is produced, and any timestamp inside the window can be restored to.
- A PITR restore **creates a sibling database service** for inspection. Cutting the
  app over is a separate, deliberate step (change `DATABASE_URL`/`DATABASE_ADMIN_URL`
  and redeploy).
- **Buckets**: no versioning and no lifecycle rules. A deleted recording is gone. The
  transcript on the capture row is the durable copy; the audio is a convenience.

## Targets

- **RPO** (how much can be lost): seconds with PITR on — the WAL archive lag. Without
  PITR: up to a day (the last differential).
- **RTO** (how long to get back): the time to restore a sibling and flip two variables.
  Measure it in the drill below and write the number here: **RTO = ___ minutes.**

## The drill: restore into scratch and query it

Run this the week PITR goes on, and again every quarter. It proves the backup is
readable and that you know the steps under no pressure.

1. In Railway, open the Postgres service → Backups → restore the most recent point in
   time to a **new** service. Note the time you started.
2. Get the sibling's connection string. Do **not** point the app at it.
3. From a local shell:

   ```bash
   SCRATCH_URL='postgresql://...sibling...' scripts/restore_drill.sh
   ```

   The script counts captures, derivations, chunks, messages and llm_calls on the
   sibling, prints the newest `captures.created_at`, and fails if the schema is not
   at `alembic head`. Compare the newest capture time with what you expect for the
   restore point.
4. Note the time you finished; that is your RTO. Delete the sibling service.
5. Record the date and the numbers at the bottom of this file.

The same script works locally against the compose database, which is how it is tested:
`scripts/restore_drill.sh` with no `SCRATCH_URL` dumps the dev database into a scratch
database on the same server, restores it, and checks it — a full dump/restore round trip
in under a minute.

## Drill log

| Date | Restore point | RPO observed | RTO (min) | Notes |
| --- | --- | --- | --- | --- |
| _(none yet)_ | | | | Run the first one the day PITR is enabled. |
