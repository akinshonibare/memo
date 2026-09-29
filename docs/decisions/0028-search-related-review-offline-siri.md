# 0028 — Meaning search, related notes, a weekly review, offline capture, and Siri

**Status:** active
**Date:** 2026-09-28

Five features that each rest on something already built. The decisions worth
recording are where each one stops.

## Search: reuse Ask's retrieval, and keep the query out of the URL

**Problem:** Home's search box matched substrings on the phone, so "dentist
appointment" missed "the dentist moved to Friday".

**Options:** substring match (the status quo); Postgres full-text only; the same
hybrid retrieval as Ask (keyword plus vector, fused with RRF, ADR 0005).
**Chosen:** hybrid. It already exists, is tested and is evaluated, and one retrieval
path means one thing to improve. It costs one query embedding per search.

**Detail that matters:** the query goes in a `POST` body, not `?q=`. A search query is a
fragment of someone's notes, and URLs end up in proxy and access logs.

## Related notes: nearest neighbours under a threshold, not a model

**Problem:** the brief calls reconciliation (a new note updating an old idea) the
hardest problem in the system. The first useful step is only *showing* that two notes
are about the same thing.

**Options:** an LLM judging pairs (accurate, but a model call for every note viewed);
nearest neighbours by embedding (cheap and deterministic); full entity resolution
(the V4 problem). **Chosen:** nearest other notes to this note's embedding centroid,
under `related_max_distance`.

**Accepting:** 0.45 is a **starting guess for voyage-4, not a measured value**. Too loose
shows noise; too tight shows nothing. It errs quiet, because a wrong "related" costs
more trust than a missing one. The scan is per user across all chunks, which is fine
at personal scale and would need an ANN query at larger scale.
**Revisit:** once there are a few hundred real notes, label 30 pairs and set the
threshold from them.

## Weekly review: facts from queries, no model

**Options:** an LLM-written reflection; a deterministic roll-up. **Chosen:**
deterministic. It shows what was captured, done, still open, coming up and new ideas,
Monday to Sunday in the user's zone. A written summary would read better and cost a
model call per view with nothing to check it against. Add it once there is an eval for
it. Still-open todos are listed, never scored (the brief's no-guilt rule).

## Offline capture: an outbox on the phone; the server already has the hard part

**Problem:** capture is the one thing memo must never refuse, and no signal turned a
capture into an error. The brief cut offline for V1.

**Why it's small:** the server's `client_token` idempotency (ADR 0012's design) already
makes a retried create a no-op. The client needs an outbox, not a sync protocol.

**Decision:** captures that fail on the network (no connection, timeouts, 5xx, 408, 429)
are written to an outbox in app documents, with files copied out of the purgeable cache.
It flushes on sign-in, on returning to the app, and every 20 seconds while anything
waits. A 4xx is a refusal that would repeat, so the item stays, visibly marked, until
the user removes it. It is never dropped silently. The server now accepts `captured_at`
(bounded to the last 90 days and not the future): a note's "tomorrow" must resolve
against when it was said, not when it synced.

**Accepting:** single device. The outbox isn't encrypted beyond iOS's own file
protection. No background sync: it waits until memo is next opened.

## Siri: App Intents in the main target, handing over through memo://

**Options:** Shortcuts plus the URL scheme only (ADR 0027); App Intents in an App
Intents extension; App Intents in the main app target. **Chosen:** the main target,
through a config plugin (`plugins/withAppIntents.js`). App Shortcuts compiled into the
app are discovered by Siri with no setup. "Take a note in memo" asks for the text,
"Record in memo" opens recording. Each intent opens memo and hands over through its own
`memo://` link, so all capture logic stays in one place (the app's outbox path). The
dictated text travels on-device only.

**Accepting:** the app opens briefly to save a dictated note. A fully background save
would need the session token readable from Swift, meaning a Keychain access group
shared with the intent. That is more attack surface for a second of screen time.

## Interview angle

> **"Make your capture app work offline."**

First check what the server already guarantees. If creates are idempotent on a
client-generated key, offline is a client outbox, not a sync engine: queue with the key,
retry with backoff, and the server de-duplicates. Then get two details right that
people miss. Carry the *capture* time, not the sync time, because anything
time-relative in the content depends on it. And classify failures: retry network
errors and 5xx, but surface a 4xx, because it will fail forever, and dropping it
silently is data loss that looks like success.
