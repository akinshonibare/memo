# 0011 — uuidv7 primary keys, generated in the application

**Status:** provisional — becomes `active` the moment [#7](https://github.com/akinshonibare/memo/issues/7) lands
**Date:** 2026-08-18

## Problem

Every table in migration 1 needs an `id`. The choice is hard to reverse — not because the
`ALTER` is difficult, but because primary keys propagate: into every foreign key, every URL,
every citation, every log line, and into the Expo client's local state. Changing it later is a
rewrite of the identity of every row in the system.

Q5 framed it as index locality versus guessability. Half of that is a real trade and half is
folklore at this scale, so they get separated before anything is chosen.

### The locality argument, and why it does not apply here

It is true, and the mechanism is worth being able to state. A monotonic key means every insert
lands on the rightmost leaf page of the B-tree, so the write hot-set is one page. A random key
scatters inserts across the whole index: every insert dirties a different page, pages split at
roughly 50% fill instead of packing to ~90%, and — the part usually missed — with
`full_page_writes` on, the first touch of a page after each checkpoint writes a full 8KB to
WAL. The cost of random keys is **write amplification**, not the eight extra bytes of storage.

Now the numbers for memo: ~20 captures/day is ~7k captures/year and maybe 30k chunks. The
whole index fits in `shared_buffers` many times over. None of the preceding paragraph will
ever be measurable on this system.

So it is not a tiebreaker. The live axes are: **guessability under a tenant boundary**, and
**who is allowed to mint an identity**.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **`bigserial` / `GENERATED ALWAYS AS IDENTITY`** | 8 bytes, perfect insert locality, free ordering, and IDs a human can say out loud — `capture 1041` is a sentence, which matters more during debugging than it looks on paper. The industry default for internal systems, and correct for most of them. | Enumerable. Post-[0010](0010-multi-tenant-schema-single-user-product.md) the tenant predicate becomes the *only* thing between `/captures/1041` and `/captures/1040`, which collapses defence-in-depth into defence-in-one. Leaks volume wherever an ID escapes, and IDs escape into citations, logs, and LLM context. Cannot be minted client-side. |
| **`uuid` v4** | Unguessable, no volume disclosure, mintable anywhere, globally unique so merging sources is free. | 16 bytes plus the write amplification above — both irrelevant *here*, but the amplification is exactly what makes v4 the wrong habit to form. No ordering, so `ORDER BY id` means nothing (little real cost: `created_at` is indexed regardless). |
| **`uuid` v7** | Everything v4 buys, plus a 48-bit millisecond prefix that restores near-sequential B-tree locality. Same 16 bytes as v4 — the ordering is genuinely free, which is what makes this an easy call rather than a trade. | **Discloses creation time** to anyone holding an ID. Usually nothing; for a journal it means a shared capture URL reveals when the entry was written. Not native before PG 18. |
| **`bigint` PK + separate `uuid` public id** | Optimal on both axes simultaneously: tight internal joins, opaque external identifier. Where large systems genuinely land. | Two identities per row forever. Every external lookup goes through a secondary index; every signature must answer "which id is this"; every join is an opportunity to use the wrong one. Real bug surface, bought to solve a problem this system does not have. |

## Decision

**`uuid` v7, column type `uuid`, generated in Python.** Postgres moves 17 → 18 at the same
time.

v7 over v4 because time-ordering costs nothing — identical width, identical opacity in
practice — so the one axis on which UUIDs lose to integers never becomes a reason to migrate.
v7 over `bigserial` because the tenant boundary committed to in [0010](0010-multi-tenant-schema-single-user-product.md)
deserves more than a single enforcement layer, and because IDs leak into places whose
disclosure is not controlled.

Three things ride along with it.

### The primary key is not the idempotency key

The tempting simplification: let the client mint `id`, make retries `ON CONFLICT (id) DO
NOTHING`, and delete `client_token`. Rejected, for two reasons.

**RLS does not conceal unique-index conflicts.** Under [0010](0010-multi-tenant-schema-single-user-product.md)
an insert that collides with another tenant's invisible row still raises a unique violation.
A client controlling the primary key can therefore probe for the existence of rows it cannot
read. Astronomically improbable with random UUIDs — and it is a channel that exists *only*
because the key was handed to the client, which is a bad reason to have a channel.

**They are different concepts.** `id` is identity; `client_token` means "this is the same
submission attempt". They come apart the moment a retry should update rather than insert. So:
server mints `id`, the client sends `client_token`, unique on `(user_id, client_token)`, as
[memo-system-design.md:564](../interview/memo-system-design.md#L564) already had it. The Expo
optimistic-UI story is served by `client_token`, not by owning the key.

### Generated in the application, not by a column default

Not to sidestep the version question — PG 18 makes `uuidv7()` available either way — but
because a database-side default means the row has no identity until flush. App-side generation
makes the ID available at construction time: it can be logged before the transaction commits,
used to build the object graph in one pass, and returned without a round trip. `uuid6.uuid7()`
is the usual package on 3.12; confirm the current API when writing [#7](https://github.com/akinshonibare/memo/issues/7)
rather than trusting this line.

### Column type `uuid`, never `text`

16 bytes and an integer comparison, versus 37 bytes and a string collation on every index
probe. This is the classic own-goal in this decision and it is worth naming so it is never
made absent-mindedly.

### Why Postgres 18

Not for `uuidv7()` — the application generates the value, so the built-in is a fallback rather
than a requirement. The real reason is that **a major version upgrade costs nothing today and
is a scheduled outage with real data later.** The dev database currently has zero user tables
and no extensions; the migration is deleting a volume. PG 18 has been GA since September 2025,
`pgvector/pgvector:pg18` exists, and Railway ([#42](https://github.com/akinshonibare/memo/issues/42))
should be provisioned to match rather than being discovered to differ.

The mechanical caveat, since it will bite exactly once: a Postgres data directory is
version-specific. Changing the image tag alone makes the container refuse to start with
*"database files are incompatible with server"*. The dev fix is `docker compose down -v`,
which is only free because the volume is empty — it will not be free again.

## What we're accepting

**A timestamp embedded in every identifier.** Anyone holding a capture ID learns the
millisecond it was created. Acceptable for a single-user journal; it is a real consideration
if V5 sharing ever puts an ID in someone else's hands.

**16-byte keys everywhere, including as foreign keys on `chunks`,** the highest-cardinality
table. At 30k rows this is noise. At 100M it would be a genuine cost, and the honest position
is that we are choosing for the system we have.

**A third-party dependency for ID generation** on Python 3.12, until either the stdlib or a
DB-side default takes over.

**IDs that cannot be read aloud.** `bigserial` gives `capture 1041`; this gives
`018f2a…-…-…`. That is a daily friction in debugging that the boring option does not have, and
it is the strongest argument the boring option has.

## Revisit when

- `chunks` passes ~10M rows **and** profiling blames index size or insert throughput —
  not before, because until then the argument is theoretical.
- Sharing arrives (V5) and an identifier disclosing "written at 02:14" stops being acceptable.
  The fix at that point is a separate opaque public id on the shared entity only, not a
  migration of every key.

## Interview angle

> **"Why not auto-increment?"**

Two reasons, and the weak one is usually given first. The weak one is guessability — sequential
IDs are enumerable in URLs, which matters once there is a tenant boundary, though the tenant
predicate is the actual control and the ID is defence in depth. The stronger one is that a
sequential key can only be minted by the database, so nothing else in the system can name a
row before it exists — no offline client, no idempotent retry that produces the same identity
twice, no cross-system merge. What I would *not* claim is that integers don't scale: they
scale better than UUIDs on every axis except those two.

> **"What is actually wrong with a random UUID as a primary key?"**

Write amplification, not size — and knowing the difference is the point. Random keys scatter
inserts across the whole B-tree, so every insert dirties a different page rather than repeatedly
touching the rightmost one. Pages split at about half fill instead of packing tight, the index
bloats, and with full-page writes each newly-dirtied page costs a full 8KB WAL record after
every checkpoint. The eight extra bytes per row are the least of it. v7 fixes this by putting a
millisecond timestamp in the high bits, which restores locality while keeping the rest of the
value random. The follow-up worth pre-empting: this only starts to matter when the index stops
fitting in memory, which for most systems is much later than the people arguing about it assume.

> **"Where do you generate it?"**

In the application, so the row has an identity before it has a database. That lets the ID appear
in a log line before the transaction commits — which is the difference between being able to
trace a failed write and only being able to trace successful ones.
