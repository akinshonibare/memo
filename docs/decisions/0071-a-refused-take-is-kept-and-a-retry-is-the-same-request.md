# 0071 — A refused take is kept, and a retry is the same request

**Status:** active
**Date:** 2026-10-25

## Problem

Akin (2026-10-24): *why am I getting failures that the audio can't be saved?*

Two voice notes in a week ended the same way in the API's request log: one
`POST /v1/recordings` answered 201, no `POST /v1/captures` ever followed, and then a
burst of `POST /v1/recordings` answered 422, a few seconds apart, until the person gave
up. Both takes were lost.

It was two faults in a chain, and the second hid the first.

1. **The upload to the bucket failed, and the app threw the reason away.** A voice
   note saves in three steps (design doc, Step 3): presign, `PUT` the bytes straight to
   the bucket, create the capture. The `PUT` came back with a status outside 2xx. The
   API never sees that request, and the app reduced the bucket's answer to the string
   "upload to storage failed", so nothing anywhere recorded *why*. It must have been a
   4xx or 3xx: a 5xx or a thrown network error counts as retryable (ADR 0028) and
   would have queued the note quietly. The cause is still unknown; it will be known the
   next time it happens, which is the point of the first change below.
2. **Every retry described a different recording.** The sheet read the length from the
   recorder's status, stopped the recorder, and on failure went back to "paused". A
   second tap on End ran the same code: expo-audio reports `durationMillis` as 0 once
   the recorder is stopped (`AudioRecorder.swift`, `totalDuration` for `.stopped`), and
   `stop()` on a stopped recorder is a no-op. So each retry sent `duration_ms: 0`, the
   schema's `ge=1` turned that into a 422 before the handler ran, the outbox classed a
   4xx as permanent, and the sheet showed FastAPI's validation JSON. Each retry also
   minted a new `client_token`, so had the first attempt in fact reached the server, the
   retry would have made a second note. Closing the sheet then deleted the one copy of
   the audio.

ADR 0028 already said *a 4xx is a refusal that would repeat, so the item stays, visibly
marked, until the user removes it. It is never dropped silently.* That was true of the
outbox and false of the record sheet, which is where a take is refused first.

## Options

Three decisions, one per link in the chain.

**What the storage error carries.**

| Option | Buys | Costs |
| --- | --- | --- |
| Status and the bucket's `<Code>` in the message | Diagnosis on the next occurrence, shown in the sheet and kept with the item | A slightly technical sentence for the person; only visible on the phone that had it |
| Report the failure to the API (`POST /v1/recordings/{id}/failed`) | Visible to the operator without the phone in hand | A new endpoint and schema, and probably a column |
| A client error-tracking service | Catches this and everything else | The first third-party service in the app; out of scope by the brief's observability rule |

**How the sheet retries.**

| Option | Buys | Costs |
| --- | --- | --- |
| Fix the take on the first End (file, length, identity) and send exactly that again | A retry is the request the server already deduplicates by `client_token`; nothing is read from a stopped recorder | One ref and a sixth phase, "kept" |
| Keep a refused take in the outbox, marked, as a waiting one already is | The brief's rule holds at the point of refusal, not only later; Try again from the Outbox | `captureOrQueue` writes on every failure, and its callers pass the identity |
| Derive the length from the Live Activity clock | Fixes the number | Not the lost file, and the clock is close rather than exact |

**What the API does with a length of 0.**

| Option | Buys | Costs |
| --- | --- | --- |
| Coerce 0 to `None` | A capture is never refused over a hint; the pipeline already fills the length from the transcript | A client sending nonsense isn't told |
| Keep `ge=1`, fix the client | The schema stays strict | Any future client with the same bug refuses captures again |
| Drop the field | One less thing to be wrong | A fresh note shows no length until it's transcribed |

## Decision

Carry the bucket's status and code in the error; fix the take on the first End and keep
it in the outbox when the server refuses it, so a retry (from the sheet or the Outbox)
is the same request with the same `client_token`; and treat `duration_ms: 0` as unknown
on the server, because the transcript is the source of truth for it anyway.

## What we're accepting

- A storage failure is diagnosable only on the device that had it. For a product with
  users one can't call, the report-to-API option is the right shape; it's parked in
  `docs/open-questions.md` (Q13) with its trigger.
- A take that storage keeps refusing sits in the outbox until the person removes it,
  taking space on the phone. That is the correct failure mode; it's what already
  happens to a text note the server refuses.
- A retry presigns a *new* recording row and key. If the first attempt's capture did
  reach the server, the retry's recording is an orphan until the unclaimed-recording
  sweep removes it (24 hours). A little waste, in exchange for never making two notes.
- The server no longer tells a client that sends `0` that it was wrong. Right for an
  optional hint; it would be wrong for anything that drives behaviour, and the
  validator's comment says which this is.

## Revisit when

- The storage reason, once seen, turns out to be systematic (a signature or
  content-type mismatch, a region redirect) rather than a flaky network. Then the fix
  is in `presign_put` or the upload headers, and this ADR gets a "What we found" note.
- A second person uses memo. Then the operator needs the failure without the phone:
  build Q13.
- A refused item is retried more than a handful of times by hand. Then "Try again" is
  the wrong shape and the outbox should back off and retry refused items itself, with
  a cap.

## Interview angle

> **"A client retries a failed upload and the server rejects the retry with a
> validation error. What went wrong?"**

The retry wasn't a retry. An idempotency key only deduplicates *identical* requests;
if the client rebuilds the body from live state (here, a recorder that now reports a
length of 0) and mints a fresh key each time, the server sees a stream of different,
individually-wrong requests, and no key saves it. The fix is to materialise the request
once (file, metadata, key) and resend that object, unchanged, however many times. Then
the server's dedup does its job, and a transient failure costs nothing.

> **"The error says 'upload failed'. What's the first change you make?"**

Make the error carry what the far side said. A failure that crosses a boundary the
server can't see (a presigned PUT straight to object storage) has exactly one witness,
the client, and the client must keep the status and the response body or the cause is
gone with it. Observability is about preserving the one fact you'll need later, not
about dashboards.

> **"Should the server accept a zero duration?"**

Classify the field first: is it a fact the server depends on, or a hint it can
reconstruct? Here the transcript gives the true length, so the client's number is a
hint, and a hint should never be grounds for refusing the thing it describes. Keep the
strictness for fields that drive behaviour.
