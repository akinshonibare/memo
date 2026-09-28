# 0010 — A multi-tenant schema under a single-user product

**Status:** active — since [#7](https://github.com/akinshonibare/memo/issues/7) (2026-09-27)
**Date:** 2026-08-18

## Problem

The brief scopes V1 as single-user with auth explicitly out. The system-design work
([memo-system-design.md](../interview/memo-system-design.md)) designs a multi-tenant product
at ~10k DAU. Both are coherent, and the gap between them was parked as Q10.

It stops being parkable at [#7](https://github.com/akinshonibare/memo/issues/7), because
migration 1 either has `user_id` or it doesn't.

Two things get conflated under "multi-user", and only one is actually on the table:

- **Product multi-user** — signup, auth, sharing, billing. Not in V1. The brief is right.
- **Schema multi-tenancy** — `user_id NOT NULL` on every user-owned table, denormalised onto
  `derivations` and `chunks`, composite indexes led by it, and a tenant predicate on every
  retrieval query.

You can have the second without the first. This ADR is only about the second.

### The argument not to use

The obvious case for deciding now is migration cost — "backfilling and re-indexing every
table including the vector index." It doesn't survive contact with the numbers:

- `ADD COLUMN user_id uuid` is metadata-only in PG 11+. Free.
- The backfill `UPDATE` rewrites every row and `SET NOT NULL` needs a full scan, but at V1
  scale (~20 captures/day, tens of thousands of chunks) that is seconds.
- Rebuilding HNSW is genuinely expensive — at millions of rows. Not at 30k.

**The real cost of retrofitting is call-site archaeology, and the failure mode.** Adding a
tenant predicate to twenty existing queries means one gets missed, and a missed tenant
predicate does not 404 — it returns another person's journal as fluent prose with a
citation. The decision is about which code gets written, not which DDL gets run.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **A. No `user_id` anywhere** | The smallest honest schema. Every index is the natural one. Nothing untested pretending to be a security boundary. Keeps [0002](0002-pgvector-not-a-vector-database.md)'s single-tenant premise true, which makes its "unfiltered ANN is fine" reasoning hold. | Phases 1–4 are written with no tenant predicate anywhere. Adding the boundary later means editing the retrieval path — the one place where a mistake is invisible — at whatever moment created the pressure to add it. |
| **B. `user_id` column, one seeded user, no enforcement** | Migration 1 is "right". Cheap. Feels like a hedge. | Cargo cult. A column nothing filters on is a column that lies. Indexes still lead with the wrong key and queries still carry no predicate, so the column is paid for and none of the protection is bought. The worst option, and the tempting one. |
| **C. Full tenant boundary, auth deferred** | `user_id NOT NULL` everywhere including denormalised onto `chunks`/`derivations`; composite indexes led by it; the current user comes from a `get_current_user` dependency that today returns the seeded user and tomorrow reads a token. Retrieval is written filtered from day one, so [#33](https://github.com/akinshonibare/memo/issues/33) measures *filtered* recall — the number that will actually be true. [#41](https://github.com/akinshonibare/memo/issues/41) becomes "how do we identify the user", not "how do we retrofit tenancy". | Complexity in service of a user who does not exist. Filtered ANN is the hard case for HNSW, so that problem is eaten in Phase 4 rather than dodged. And a boundary exercised by one user is **untested** — it means nothing unless the tests force a second one. |
| **D. Schema-per-tenant or DB-per-tenant** | Isolation that cannot be lost by forgetting a `WHERE`. Per-tenant delete and export ([#49](https://github.com/akinshonibare/memo/issues/49)) become `DROP SCHEMA`. No filtered-ANN problem at all: each tenant's HNSW index contains only their rows. | Migrations × N schemas, connection-pool explosion, cross-tenant analytics impossible. The right answer for 50 enterprise tenants with contractual isolation; flatly wrong at 10k DAU. Named here because it is the option an interviewer will reach for. |

## Decision

**C.** The schema is multi-tenant from migration 1; the product stays single-user with auth
deferred to [#41](https://github.com/akinshonibare/memo/issues/41).

The deciding argument is what it does to the **ask** path rather than to the schema. Phase 4's
retrieval evals are the highest-value measurement in this build, and `recall@k` unfiltered is
a different quantity from `recall@k` with `WHERE user_id = ?` pushed into an HNSW scan. Under
A, the easy version gets measured, then the query shape changes and every number is re-earned.

Two conditions, without which this is theatre rather than a decision:

1. **Every integration test seeds two users**, and at least one asserts that user B's `/ask`
   returns zero of user A's chunks. If no test fails when the tenant predicate is deleted,
   the boundary does not exist.
2. **Drift is prevented by the database, not by discipline.** `chunks.user_id` can disagree
   with `captures.user_id` — that is what denormalisation means. Enforce it structurally:
   `UNIQUE (id, user_id)` on `captures`, then
   `FOREIGN KEY (capture_id, user_id) REFERENCES captures (id, user_id)` on `derivations`
   and `chunks`. A mismatched row is then rejected by Postgres instead of by code review.

### What this ADR does not decide: RLS

Worth separating, because it changes what migration 1 must contain.

- **Expensive to retrofit** — the column, the index leading keys, the query shapes. Decided
  here.
- **Cheap to retrofit** — row-level security itself. `ENABLE ROW LEVEL SECURITY` and
  `CREATE POLICY` are metadata-only: no table rewrite, no reindex.

RLS also needs `SET LOCAL app.user_id` inside the request transaction, which is
[#6](https://github.com/akinshonibare/memo/issues/6)'s session-lifecycle work, and a
non-owner database role with `FORCE ROW LEVEL SECURITY` — the table owner bypasses its own
policies silently, including in the test database, which is the exact way this gets believed
without being true. The policy predicate should read
`current_setting('app.user_id', true)::uuid`; the `true` makes a missing setting return NULL
rather than raise, and a NULL comparison matches no rows, so the failure mode is fail-closed.

Sequence: boundary in migration 1 ([#7](https://github.com/akinshonibare/memo/issues/7)),
RLS once there is a session to hang it on ([#6](https://github.com/akinshonibare/memo/issues/6)).

**Landed (2026-09-27), with one deviation.** Migration 0001 enables RLS on `users` and
`captures` with `NULLIF(current_setting('app.user_id', true), '')::uuid` (the `NULLIF`
matters: after a `SET LOCAL` ends, the setting reads as `''`, and `''::uuid` raises rather
than matching nothing). `memo.db.get_session` calls `set_config(..., true)` inside the
request transaction. The app connects as a non-owner role `memo_app`. **`FORCE ROW LEVEL
SECURITY` was not applied**: forcing it makes the owner fail closed too, so every migration
seed and every future backfill would have to bind a user first — the "why is this empty"
tax this ADR already names. The owner-bypass trap is closed differently: a test asserts the
API's connection is `memo_app` and not a superuser, so pointing tests at the owner URL goes
red instead of silently passing. Two-user isolation, fail-closed on a missing bind, and
`WITH CHECK` rejecting a forged `user_id` each have a test.

## What we're accepting

**A boundary that V1's only user will never exercise.** It is dead weight in the mainline and
alive only in tests. That is a real cost and the honest name for it is *insurance* — the
premium is paid every time a query is written and the payout only ever arrives if there is a
second user.

**Denormalised `user_id` on `derivations` and `chunks`**, which is deliberate denormalisation:
the same fact stored in three places so the tenant filter never requires a join on the
retrieval hot path. The composite FK above makes it non-drifting, at the cost of an extra
unique index on `captures` and slightly more surprising DDL.

**Filtered ANN as the default query shape, in Phase 4, on purpose.** pgvector's HNSW
post-filters — the graph is searched and the predicate applied to what comes back — so a
selective filter with a low `ef_search` can return fewer than `k` rows. With one user the
filter matches everything and this is invisible, which means the eval suite must be the thing
that catches it, not production. (pgvector 0.8's iterative index scans are the mitigation to
reach for; partitioning `chunks` by `user_id` hash is the heavier one. Neither is decided
here — verify current behaviour at [#30](https://github.com/akinshonibare/memo/issues/30)
rather than trusting this sentence.)

**Pressure on [0002](0002-pgvector-not-a-vector-database.md).** Its reasoning assumed one
tenant and treated filtered search as an edge case. That premise is now weaker by our own
choice. 0002's conclusion still holds at V1 scale, but its revisit trigger has been sharpened
accordingly.

## Revisit when

- **By [#9](https://github.com/akinshonibare/memo/issues/9), no test fails when the tenant
  predicate is removed.** Then this is decoration, not a boundary — delete it and drop back
  to option A rather than keeping a comforting column.
- **A second real user exists.** This promotes to `active`/`load-bearing`,
  [#41](https://github.com/akinshonibare/memo/issues/41) becomes real auth rather than
  identity, and the brief's single-user scope is genuinely amended rather than nominally.
- **Filtered recall@k at [#33](https://github.com/akinshonibare/memo/issues/33) degrades
  materially versus unfiltered at the same `ef_search`** — that is the concrete signal that
  the multi-tenant retrieval story needs partitioning or a different index, and it is the
  first real argument for revisiting 0002.

## Interview angle

> **"You're building a single-user app. Why does your schema have tenants?"**

Because the schema is the part that is expensive to change and the product is not. Two costs
get confused here. Adding a column later is nearly free — it is metadata-only in modern
Postgres, and the backfill at my data volumes is seconds. What is not free is adding a tenant
predicate to a retrieval path that was written without one, because the failure mode is
silent: a missed `WHERE user_id = ?` does not throw and does not 404, it returns someone
else's data as a fluent answer with a citation attached, and no test that was written for a
single-tenant world will catch it. So I pay for the boundary at the point where it is a
convention rather than a migration. The thing that makes this a decision instead of
superstition is that my integration tests seed two users, and one of them asserts the
negative — B's query returns none of A's rows. If deleting the predicate does not turn a test
red, I have not built a boundary, I have built a column.

> **"Why not a schema per tenant?"**

It is genuinely the strongest isolation available and I would use it for tens of enterprise
tenants with contractual separation, because you cannot lose it by forgetting a `WHERE`. It
fails on cardinality: every migration runs N times, connection pooling degrades because
pooled connections are not fungible across schemas, and cross-tenant work becomes impossible.
For a consumer-shaped product the shared-table-plus-`user_id` model is the default, and the
job is then to move enforcement below the application — row-level security, so a forgotten
predicate returns zero rows instead of someone else's.

> **"Where does `user_id` come from?"**

The token, always. Never the request body, query string, or path. A `user_id` in a body is a
parameter an attacker controls; the isolation layers below it are defence in depth, not the
primary control. That is why the API signatures in my design take no `user_id` argument at
all — it arrives via a dependency that reads the authenticated principal, and there is no
call site where a caller *could* pass the wrong one.
