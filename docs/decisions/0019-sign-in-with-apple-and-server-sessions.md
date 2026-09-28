# 0019 — Sign in with Apple, with server-issued sessions

**Status:** active — since 2026-09-28
**Date:** 2026-09-28
**Supersedes:** [0018](0018-static-bearer-token-for-v1.md)

## Problem

0018 gated the API with one static bearer token and named its own revisit trigger: a
second client, a second user, or the token becoming a nuisance. The trigger fired on day
one of using the app. Installing it on a phone meant copying a 64-character secret out of
Railway's variables and pasting it into a settings screen — a step no product would ask of
anyone, and one that would have to be repeated on every device and after every rotation.

Two things need to change together: **identity** (which user is this, from something the
user can prove rather than something we handed them) and **sessions** (a per-device
credential the server can revoke without touching any other device).

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Sign in with Apple** | One tap on an iOS device. Apple returns a signed identity token with a stable per-app user id (`sub`); the server verifies it against Apple's published keys — no password, no email delivery, no vendor account beyond the developer account we already have. Apple requires it anyway once any third-party sign-in exists in an App Store build. | iOS-only identity. An Android or web client would need a second provider (Apple's web flow, or Google) and account linking. The email Apple gives us may be a private relay address. |
| **Email magic link** | Works on every platform, no platform SDK. | An email vendor and key, deliverability as a failure mode, and a link-open-on-phone flow that is slower than one tap. |
| **Passwords** | Nothing depends on a third party. | We'd own hashing, reset flows, breach-response — the whole surface everyone else outsources. Worst of the four for a one-person product. |
| **Hosted auth (Clerk, Auth0, Supabase Auth)** | Fastest to a demo; multi-provider out of the box. | Another vendor, a monthly bill above the free tier, and the user table lives in their system, which cuts across [0010](0010-multi-tenant-schema-single-user-product.md). |

For the session itself, once identity is proven:

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Opaque token, hashed in Postgres** | Revocable per device, no signing key to manage, a `sessions` row is an audit trail for free. Verification is one indexed read. | A database read per request (the tenant-bound request session comes after, so two connections per request). |
| **Signed JWT** | Stateless verification. | Revocation needs a denylist, which is the database read we were avoiding; and a signing secret to rotate. |

## Decision

**Sign in with Apple, exchanged at `POST /v1/auth/apple` for an opaque session token that
the server stores only as a SHA-256 hash.** The app keeps the raw token in the Keychain
and sends it as `Authorization: Bearer`. `get_current_user` resolves it to a `user_id`
under the system RLS bypass, then the request's own session binds that user. Logout
revokes one session; other devices stay signed in. Sessions expire after 90 days.

The first Apple sign-in ever **claims the seeded user** from migration 0001, so the
captures made before auth existed keep their owner. Every later identity gets its own
`users` row. The static token, `API_TOKEN`, and `current_user_id` are gone.

## What we're accepting

**iOS-only identity.** A second platform means a second provider and an account-linking
story. Not needed until a second platform exists.

**Two database connections per authenticated request** — one to resolve the session, one
bound to the tenant. Measured in single-digit milliseconds on Railway's private network.
Cache the lookup in Redis if it ever shows up in the request logs.

**The seed-user claim is a one-time bootstrap rule in code.** It is guarded by a setting
and does nothing once the seed user has an `apple_sub`, but it is the kind of special case
that gets forgotten. It's here so the ADR remembers it.

**Apple's identity token is verified, not exchanged.** We don't call Apple's token
endpoint for a refresh token, so we can't detect a user revoking the app in their Apple ID
settings until the session expires. Acceptable for a personal app; a server-to-server
notification endpoint is the fix if it ever matters.

## Revisit when

- A client that isn't iOS needs to sign in.
- A session lookup shows up in request latency — the Redis cache is the first move.
- Apple's revocation matters (shared devices, a compliance requirement): add the
  server-to-server notification endpoint and revoke sessions from it.

## Interview angle

> **"How does login work, and why not JWTs?"**

Apple proves identity: the app gets a signed identity token, the server verifies the
signature against Apple's JWKS, checks issuer, audience (our bundle id) and expiry, and
reads the stable `sub`. That's the last time Apple is involved. The server then issues its
own opaque session token, stores only its hash, and the phone keeps the raw value in the
Keychain. Every request costs one indexed read to turn the token into a user id, and
revocation is a timestamp on that row. JWTs would save the read but bring back a
signing secret and a denylist for revocation — a denylist that *is* the read I was trying
to avoid, so I'd only be paying for statelessness I don't use. The part I'd volunteer is
the bootstrap: the first sign-in claims the pre-auth user so existing data keeps its
owner, and that's a one-time rule I'd want to be able to point at in an ADR rather than
have someone discover in the code.
