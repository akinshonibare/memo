# 0018 — A static bearer token gates V1; identity still comes from a dependency

**Status:** provisional — becomes `active` when [#9](https://github.com/akinshonibare/memo/issues/9) lands
**Date:** 2026-09-27
**Closes:** Q7, [#41](https://github.com/akinshonibare/memo/issues/41)

## Problem

The brief scopes auth out of V1 — "single user, optionally Tailnet-only." That was a
*scoping* decision when the API would only ever be reachable from a private network. Q8 made
the deploy target Railway with no tunnel, which means the API has a public URL, and at that
moment Q7's own warning applies: **"public-with-no-auth is not defensible."** A journal and
its audio behind a URL anyone can guess is a security decision, not a scope decision.

Two different things get called "auth" and only one is on the table:

- **Gating** — can this request reach the API at all?
- **Identity** — *which user* is this request acting as?

[0010](0010-multi-tenant-schema-single-user-product.md) already settled identity: the
current user comes from a `get_current_user` dependency that today returns the one seeded
user, never from a body, query, or path. This ADR is about gating.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Nothing + Tailnet-only binding** | Zero code. Network-level isolation is stronger than any application check. What the brief had in mind. | Requires a Tailscale node in front of Railway (a sidecar, or Railway's private networking plus a subnet router). It's the homelab plan wearing a different hat — Q8 removed exactly this kind of infrastructure from V1. And the Expo client on a phone has to be on the tailnet too. |
| **Static bearer token** | One secret, one header, one FastAPI dependency. `Authorization: Bearer <token>` compared in constant time against an environment variable. The Expo client stores it in secure storage. Rotation is a redeploy. Total implementation: an afternoon, most of it tests. | It is a shared secret, not identity — it can't distinguish two devices, can't be revoked per device, and if it leaks the only fix is rotate-and-redeploy. There is no login, so there is nothing to log out of. |
| **Real auth** (OAuth/OIDC via an identity provider, or passkeys, sessions, refresh tokens) | Identity, revocation, multiple devices, the thing V2 needs anyway. Interviewers expect it. | The full weight of a login flow in the client and a token-verification path in the API, for a product with one user who is also the developer. Every hour here is an hour not spent on extraction and retrieval. Deferred by the brief on purpose. |

## Decision

**A static bearer token, checked in a dependency applied to every route except `/health`.**
The dependency does two things and keeps them separate: `require_token` gates the request;
`get_current_user` resolves identity — today the seeded user, tomorrow whatever a real token
carries. No route signature takes a `user_id`.

Constant-time comparison (`secrets.compare_digest`), the token read once via `Settings`, a
`401` with no body detail on mismatch, and the `/health` liveness probe left open so Railway's
health check doesn't need a secret.

## What we're accepting

**No identity, no revocation, no audit trail per device.** If the phone is lost, the token
is rotated by changing an environment variable and redeploying, and every other client (there
are none) breaks with it.

**A secret in the Expo client's storage.** Secure storage on iOS is good; it is still a
long-lived credential on a device. This is exactly the thing real auth's short-lived tokens
exist to avoid.

**The tenant boundary is untested against a real adversary.** With one token there is one
user; [0010](0010-multi-tenant-schema-single-user-product.md)'s two-user test is still the
only thing exercising isolation.

**We are calling this "not auth" and it will be tempting to leave it forever.** The revisit
trigger below is the guard against that.

## Revisit when

- **A second real user exists** — the same trigger that promotes 0010 to `active`. At that
  point this ADR is superseded by real identity, and the `get_current_user` seam is where it
  plugs in.
- Any client other than the Expo app needs access (a CLI, a browser). Two clients sharing
  one secret is where "rotate and redeploy" stops being an acceptable revocation story.
- The token leaks, once. Rotation is the immediate fix; the incident is the signal that
  per-device credentials are overdue.

## Interview angle

> **"You said auth was out of scope. Then why is there a token?"**

Because the moment the API got a public URL, "no auth" stopped being a scoping decision and
became a security one, and I'd rather say that than pretend a journal behind a guessable URL
is fine. The distinction I'd draw is gating versus identity. Gating — can this request reach
me at all — is one static bearer token compared in constant time, an afternoon's work, and it
closes the actual exposure. Identity — *which* user this is — was already settled: it comes
from a dependency, never from the request body, so when real auth arrives it plugs into one
function and no route changes. What I'd volunteer is the cost: a shared secret can't be
revoked per device and rotating it is a redeploy. That's acceptable with one user and one
phone, and the ADR names the exact trigger — a second user, or a second client — at which it
stops being acceptable.
