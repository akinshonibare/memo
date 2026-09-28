# Deploying to Railway

One environment, one image, four managed things: Postgres, Redis, a Bucket, and two
services built from this repo (issue #42, Q8).

## Services

| Service | Settings | Start command |
| --- | --- | --- |
| `api` | Dockerfile build, health check `/health`, pre-deploy `alembic upgrade head`, restart on failure | `uvicorn memo.main:app --host 0.0.0.0 --port $PORT` |
| `worker` | Dockerfile build, restart on failure | `arq memo.worker.WorkerSettings` |

Both use the same `Dockerfile`. Railway deprecated `railway.json` config files in
September 2026, so these settings live on the services themselves (set through the
Railway MCP/CLI at provisioning time; visible under each service's Settings). Migrations
run once per API deploy as the pre-deploy command, as the owner role.

Provisioned 2026-09-28: project `memo` (id `bfce4149-…`), environment `production`,
services `Postgres`, `Redis`, bucket `recordings` (ams), `api`, `worker`.

## Managed resources

- **Postgres**: Railway's `postgres-ssl:18` image ships pgvector and pgBackRest. Turn
  on point-in-time recovery (see `backups-and-restore.md`).
- **Redis**: arq's broker and the rate limiter. Nothing the product reads lives here.
- **Bucket**: recordings. Railway Buckets have no lifecycle rules; the worker's hourly
  sweep expires unclaimed uploads.

## Variables

Set on both services unless noted. Use Railway variable references for the managed
resources so a rotation propagates.

```
APP_ENV=production
LOG_LEVEL=INFO
DATABASE_URL=postgresql+asyncpg://memo_app:<APP_DB_PASSWORD>@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}
DATABASE_ADMIN_URL=postgresql+asyncpg://${{Postgres.PGUSER}}:${{Postgres.PGPASSWORD}}@${{Postgres.PGHOST}}:${{Postgres.PGPORT}}/${{Postgres.PGDATABASE}}
APP_DB_PASSWORD=<openssl rand -hex 24>
API_TOKEN=<openssl rand -hex 32>            # api only; the Expo app carries it
REDIS_URL=${{Redis.REDIS_URL}}
S3_ENDPOINT=${{Bucket.ENDPOINT}}
S3_BUCKET=${{Bucket.BUCKET}}
S3_ACCESS_KEY_ID=${{Bucket.ACCESS_KEY_ID}}
S3_SECRET_ACCESS_KEY=${{Bucket.SECRET_ACCESS_KEY}}
S3_REGION=${{Bucket.REGION}}
S3_ADDRESSING_STYLE=auto
ANTHROPIC_API_KEY=...
VOYAGE_API_KEY=...
DEEPGRAM_API_KEY=...
```

The first `alembic upgrade head` creates the `memo_app` role with `APP_DB_PASSWORD`;
`DATABASE_URL` must use that same password. Postgres on Railway uses TLS; asyncpg
negotiates it when the server requires it, so no `?ssl=` suffix is needed.

## First deploy, in order

1. Create the project and the three managed resources.
2. Create `api` from the repo; set its config file path; set the variables; deploy.
   The pre-deploy migration creates the schema, the app role, and the seeded user.
3. Create `worker` from the same repo with `railway.worker.json`; same variables
   minus `API_TOKEN`; deploy.
4. `curl https://<api-domain>/health` → 200. Then `POST /v1/captures` with the token.
5. Set the seeded user's timezone: `UPDATE users SET timezone = 'Europe/London';`
   (the morning view builds at 04:00 in that zone).
6. Turn on PITR and run the restore drill once.

## What is deliberately not here

No staging environment, no blue/green, no metrics backend (CLAUDE.md's observability
scope). Rollback is Railway's "redeploy previous". A bad migration is rolled back with
`alembic downgrade` from a local shell against `DATABASE_ADMIN_URL`.
