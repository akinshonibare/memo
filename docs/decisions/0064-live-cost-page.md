# 0064 — A live cost page for the operator, served by the API

**Status:** active
**Date:** 2026-10-01
**Amends:** [0063](0063-daily-ai-limit.md) (how the operator sees costs)

## Problem

Akin wanted a dashboard of his costs and his users' usage, showing who's close to their
daily limit. The first version (a private claude.ai page) was a snapshot that only
changed when he ran `python -m memo.admin.costs --json` in Railway and pasted the
output. He asked for one that refreshes itself.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| **A page served by the API at /admin/costs, behind an operator password** | Live on every load; works from any browser, a phone's included; nothing scheduled anywhere | A new public route to secure; a secret to keep in Railway |
| The claude.ai page, republished hourly by a scheduled job on Akin's Mac through a key-protected JSON route | No HTML served by memo | Up to an hour stale, only while the Mac is on, and the same key-protected route anyway |
| A Costs screen in the app for operator accounts | Uses the existing Apple sign-in; no new secret | An operator role in the data model; offered first and not chosen |
| A metrics stack (Grafana and friends) | The industry default | Out of scope by the quality bar until it hurts; far more than a page of sums |

## Decision

**`GET /admin/costs` (the page) and `GET /admin/costs.json` (its data), outside `/v1`
and out of the OpenAPI schema, behind HTTP Basic with the password `OPERATOR_TOKEN`
(any username).** Browsers prompt for Basic auth natively and keep it for the session,
and it travels in a header, never in a URL that a proxy log or browser history would
keep. Unset, the routes are a 404: the page exists only where the operator turned it on.

- The password is compared in constant time. Wrong passwords are rate-limited per
  address (10 a minute, failing closed). A user's own bearer session is not accepted.
- The page is the snapshot page's design with live data rendered into it on each
  request; it reloads itself every minute while it's in front. Data comes from
  `memo.admin.costs` (the same functions as the command), read as the system.
- `Cache-Control: no-store`, `noindex`, `Referrer-Policy: no-referrer`, a CSP allowing
  only its own inline code and Google Fonts, `frame-ancestors 'none'`. A display name
  can't close the page's script tag, and is escaped where it becomes HTML.

## What we're accepting

- **A shared secret, not an identity.** Anyone with the password sees every user's
  spend by name. Rotate it in Railway if it leaks; there's no per-person access.
- **Names of users on a page reachable from the internet**, behind the password. The
  page shows spend, not content.
- **This is a cost page, not observability.** The quality bar keeps dashboards out of
  scope; this one exists because cost control is in scope, and it shows only the cost
  log.

## Revisit when

- A second operator needs access: move to operator accounts (the in-app option) rather
  than sharing the password.
- The page is slow: the per-user budget query runs once per user per load; batch it.

## Interview angle

> **"How would you put an internal admin page on a production API?"**

Keep it off the public API surface (its own prefix, not in the schema), off by default
(no secret, no route), and behind auth that doesn't leak through URLs: HTTP Basic over
TLS or real SSO. Rate-limit failures, compare secrets in constant time, and send
no-store, noindex and a tight CSP. Treat anything user-typed that lands in the page as
hostile, including inside a script block.
