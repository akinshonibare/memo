# 0051 — Live words while a voice note records: display only, over Ask's socket, with a patched expo-audio

**Status:** active
**Date:** 2026-09-29

## Problem

Recording a voice note shows an orb and a clock. You can't see that memo is hearing
you, or where you were before a pause, until the note uploads and is transcribed.
Roadmap item 2 was the words appearing as you speak.

memo already streams speech: Ask's hands-free mode (ADR 0025) sends 16 kHz PCM over
`/v1/voice/live` to Deepgram and gets partial and final words back. The problem is on
the phone. expo-audio's stream, started while its recorder is recording, **takes over
the audio session**: `start()` sets the category to `.record` / `.measurement`, and
`stop()` deactivates the session. The first reconfigures the session under a take in
progress; the second ends the take. Neither is acceptable for the note itself.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Transcribe the file in chunks while recording (upload every 10 s) | No native change | Seconds late, choppy words, many uploads, and a recorder can't be read mid-file safely |
| Record from the stream instead (write PCM to a file, drop the recorder) | One audio path | Rewrites ADR 0038's background recording, Live Activity and interruption handling, all proven on the phone |
| On-device speech (SFSpeechRecognizer) | Free, offline | Another native module, a second permission, and words that differ from Deepgram's |
| **Patch expo-audio** (patch-package): a stream started while a recorder holds the session shares it, and never deactivates a session it didn't set up | Three small Swift changes; recorder, background recording and Live Activity untouched | A patch to carry on every expo-audio upgrade, and a native build |

## Decision

**Patch expo-audio so a stream shares a recording's session, and show the words from
Ask's live socket in the record sheet - display only; the recording stays the note.**

- **The patch** (`apps/mobile/patches/expo-audio+57.0.5.patch`, applied on `postinstall`,
  so EAS applies it too): the stream is told whether any recorder is prepared, recording
  or paused. If one is, it uses the session as it is; otherwise it behaves exactly as
  before (Ask's voice mode is unchanged). It deactivates the session on stop only if it
  activated it. Start the stream only once the recorder is recording.
- **The server** is unchanged apart from a `purpose: "note"` on the start message, logged
  with the audio seconds, so dictation's cost is visible apart from Ask's.
- **The sheet** (`liveWords.ts`, `RecordSheet`) shows the words so far, scrolled to
  the newest, in one colour (in unbroken speech Deepgram holds everything as "being
  said" until a pause, so a lighter colour for it turned the whole text grey). It streams only while
  recording **with the app open**: paused, backgrounded, or saving, the socket closes
  and what was heard stays on screen; coming back opens a new one.
- **It fails quietly.** No socket, no words: the sheet says "Live words aren't available
  right now. The recording is fine." and records as ever.
- **The words are never saved.** The uploaded recording is transcribed as before
  (batch, with keyterms, word timings), and that is the note's text.

## What we're accepting

- **Speech is transcribed twice** while the sheet is open: once live (about half a cent
  a minute, Nova-3 streaming) and once in batch. Worth it for seeing you're heard; the
  cost log's `purpose` says what it adds up to.
- **The live words and the note's text can differ.** Batch has the whole recording and
  is more accurate; the live words are a preview.
- **A patched dependency.** An expo-audio upgrade must re-apply or drop the patch;
  patch-package fails the install loudly when it no longer applies, which is the point.
- **Only verifiable on a real build.** The simulator shares the Mac's microphone, which
  is close but not the same as an iPhone's session behaviour.

## Revisit when

- **expo-audio ships stream-alongside-recorder support**: drop the patch.
- **Akin wants the live words kept** (instant text before the upload finishes): they
  could stand in until the batch transcript arrives.
- **The live cost matters**: stream only when the sheet has been open a few seconds.

## Interview angle

> **"How do you show live captions while recording audio on iOS?"**

Know the audio session: it's one per app, and whoever configures it last wins. Two
consumers of the microphone can coexist only if the second shares the first's
category and never deactivates what it didn't activate. Keep the recording as the
source of truth and treat live text as a preview, so a dropped socket costs a preview,
never the recording. And when a library almost does what you need, a small, documented
patch applied at install time beats a rewrite, as long as the upgrade path fails loudly.
