# 0046 — Deploy on merge: Railway follows `main` after CI; the app gets OTA updates the same way

**Status:** provisional
**Date:** 2026-09-29

## Problem

Merging to `main` deploys nothing. Someone has to check out a clean `origin/main`, run
`railway up` for the api, wait for it (twenty minutes on a slow day), then run it again
for the worker, and publish an EAS update by hand for app changes. Every step has already
gone wrong once:

- **Merged is not live.** The handoff and status notes track "merged" and "deployed" as
  separate facts, and the only way to know whether `main` is running is to compare
  commits in Railway.
- **Deployed from the wrong tree.** TestFlight builds 12 and 13 came from a branch older
  than a fix already on `main`, and were rejected.
- **Order by hand.** The api must migrate before the worker runs new code. That is a rule
  a person remembers, until they don't.

The real problem isn't typing: **what's running isn't a function of `main`.** It depends
on who deployed what, from where.

## Options

**Server**

| Option | Buys | Costs |
| --- | --- | --- |
| Manual `railway up` (today) | Full control over when | Drift, forgotten deploys, deploys from dirty trees, ordering by memory |
| **Railway GitHub autodeploy with Wait for CI**: each service follows `main` and deploys once every GitHub Actions workflow on the commit passed. The Railway default | No deploy token anywhere; builds where they already build; rollback stays one click in Railway; skipped on red CI | api and worker deploy independently, so they can't be ordered |
| GitHub Actions deploy job (`railway up` api, then worker, after CI) | Order by construction; deploy logs next to CI | A long-lived Railway token in GitHub secrets; uploads from a runner instead of a git source; one more workflow to maintain |

**App**

| Option | Buys | Costs |
| --- | --- | --- |
| Manual `eas update` (today) | Control | Same drift, plus the fingerprint check done by hand |
| **EAS Workflows on push to `main`**: fingerprint, find the build with that fingerprint, publish an update to `production` only if one exists | JavaScript changes reach the phone on merge; a native change can never be pushed as an update | Expo's GitHub app on the repo |
| Auto-build and submit native changes too | Fully hands-off | A TestFlight build on every native change, and App Review is a human step anyway |

## Decision

**Railway's GitHub autodeploy with Wait for CI on both services, and an EAS workflow that
publishes an OTA update on merge when a compatible build exists. Native builds stay a
deliberate command.**

- **Ordering is replaced by a lock.** Both services run `alembic upgrade head` as their
  pre-deploy command. `alembic/env.py` takes a Postgres advisory lock first, so whichever
  service gets there second waits, then finds the schema at head and does nothing.
  Checked: two concurrent upgrades without the lock fail ("column already exists"); with
  it, both succeed.
- **Watch paths**: the services redeploy only for server changes (`src/`, `alembic/`,
  `pyproject.toml`, `uv.lock`, `Dockerfile`, `alembic.ini`), so an app-only or docs-only
  merge doesn't rebuild the server.
- **Wait for CI** uses the existing `ci` workflow, which already runs on push to `main`.
  A red `main` (checks, evals, the app job) is not deployed.
- **App**: `apps/mobile/.eas/workflows/deploy-on-merge.yml`, on push to `main` touching
  `apps/mobile/**`: fingerprint → `get-build` (iOS, `production`) → `update` on channel
  `production`, only if a build with that fingerprint exists. A native change finds no
  build, publishes nothing, and waits for `eas build`.

## What we're accepting

- **Every merge ships.** "Merge now, deploy tomorrow" goes away; a change that shouldn't go
  live waits unmerged. For one person this is what's wanted.
- **Migrations must be backward-compatible.** For a minute the old worker can run against
  the new schema, or the new worker against the old api. Expand, then contract: add a
  column in one release, stop using the old one in the next, drop it in a third. That was
  already the safe way; it's now required.
- **No staging.** CI and the evals are the only gate before production. That was already
  true of the manual deploys; now it's also fast.
- **The Railway and Expo GitHub apps get read access to the repo**, a permission Akin
  grants once in each.

## Revisit when

- **A migration can't be made backward-compatible**: turn autodeploy off for that merge,
  or add a GitHub Actions deploy that orders the services.
- **A second environment** (staging) is wanted: Railway environments per branch.
- **Native changes get frequent enough to hurt**: add a build-and-submit job gated on the
  fingerprint missing.

## Interview angle

> **"How do you deploy? How do you make sure a migration and the new code don't race?"**

Deploy on merge, gated on CI, from the git source rather than from someone's laptop, so
what's running is a function of `main`. When two services share a schema and deploy
independently, you can't rely on ordering: make migrations idempotent under concurrency
with an advisory lock, and make every migration backward-compatible (expand/contract), so
either version of the code works against either version of the schema for the minute they
overlap. For mobile, ship JavaScript over the air only when the native fingerprint
matches a build already in users' hands, because an update that needs native code it
doesn't have crashes on launch.
