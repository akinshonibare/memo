# 0039 — Word timings live on the capture, next to the text they belong to

**Status:** active
**Date:** 2026-09-29

## Problem

Deepgram returns a start and end time for every word it transcribes. The transcribe stage
kept the text and threw the timings away. With them, the app can follow a transcript as
the recording plays, lighting up each word as it's said, and jump to a word when it's
tapped. This is the "karaoke" playback.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| **A nullable JSONB column `captures.words`: `[[start_ms, end_ms, word], ...]`** | One row holds the text and its timings, written in the same statement and replaced together on a retry; one migration, no joins | A column most rows leave NULL; the detail response grows by a few KB for a long note |
| A `capture_words` table, a row per word | Queryable per word | Nothing needs to query a single word. Two writes that can disagree; thousands of rows per long note. |
| Store Deepgram's raw response | Nothing lost | Stores a vendor's format, with fields memo doesn't use, next to private content |
| Re-transcribe on demand when a note is played | No storage | Pays Deepgram on every play; slow |

## Decision

**`captures.words JSONB NULL` (migration 0015), written by the transcribe stage alongside
`text`, and returned only by `GET /v1/captures/{id}`.**

- **Parsing** uses `punctuated_word` when smart formatting provides it, so the words
  match the transcript as shown. A malformed entry is skipped: the words are a nicety, and
  never a reason to fail the note.
- **An empty list** means transcribed with no words. **NULL** means not a voice note, or
  transcribed before this change. Old notes aren't backfilled: that would mean paying to
  transcribe every recording again, so they play without following along.
- **Not in the capture list:** the list never plays anything, so it doesn't carry the
  bytes.
- **The app degrades gracefully.** An older server sends no `words`, a new server's extra
  field is ignored by an older app, and a note without words plays as before.
- **In the app,** the playing note's text becomes the word-by-word view: spoken words in
  full colour, the current one in amber, the rest quieter. A tap seeks. The player reports
  position every 100ms.

## What we're accepting

- **Export doesn't include the timings.** They're derived from the recording, and the
  export carries the text.
- **Timings belong to Deepgram's transcript.** If transcript editing ever arrives, an
  edited text no longer matches its words, and the view has to fall back to plain text.
- **Row size.** A 10-minute note is about 1,500 words, around 40KB of JSON, on one row.
  Fine at this scale.

## Revisit when

- Transcripts become editable: drop or realign the words on edit.
- Search needs "jump to where I said X": index the words, perhaps in their own table.

## Interview angle

> **"Where would you store per-word timestamps from a speech-to-text API?"**

Next to the transcript they index, and written in the same transaction, because they're
one fact: the text and the timings must never disagree. Do that first, and normalise only
when something needs to query individual words. Make the new field nullable with no
backfill when backfilling costs money for little value. Keep it off list endpoints that
don't use it, and make every client tolerate its absence, so server and app can ship in
either order.
