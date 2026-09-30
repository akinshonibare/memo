# Decision records

**Nothing here is set in stone.** These are provisional by default — written down so that
when we change our minds we know *what we were trading away*, not so that we're bound to
them. We build step by step; a decision only becomes real at the phase where code depends
on it.

The point is not the decision — it's the **rejected options**. A decision with no
documented alternatives is indistinguishable from an accident.

## Status vocabulary

| Status | Means |
| --- | --- |
| `provisional` | Written down, not yet load-bearing. Change freely — just update the file. |
| `active` | Code depends on it. Changing it costs work; say what work. |
| `under revision` | A premise broke. Don't cite it until it's replaced. |
| `superseded by NNNN` | Replaced. Kept, never deleted — the reversal *is* the lesson. |
| `load-bearing` | Reversing it means rewriting the app. Rare, and marked as such. |

Promote `provisional` → `active` when you write the first line of code that assumes it.
That moment is worth noticing.

## How to change a decision

Don't edit the reasoning to match the new answer — that erases the learning. Instead:

1. Set the old ADR's status to `superseded by NNNN`.
2. Write the new one, and give it a **"What changed"** section: which premise stopped being
   true, and whether the original reasoning was *wrong* or just *overtaken*.

Being able to say "this was right, then the world moved" versus "I misjudged this" is
the whole point of writing them down. Q8 already produced one of these on day one.

## Format

```markdown
# NNNN — <decision, as a claim>

**Status:** provisional | active | under revision | superseded by NNNN | load-bearing
**Date:** YYYY-MM-DD

## Problem          What we actually needed. Not the tool. The problem.
## Options          Table: option / what it buys / what it costs.
## Decision         One sentence.
## What we're accepting   The downside, plainly. Mandatory. No downside = not understood.
## Revisit when     A concrete, observable trigger.
## Interview angle  The question this maps to, and the 30-second answer.
```

## Index

| # | Decision | Status |
| --- | --- | --- |
| [0001](0001-python-not-rails.md) | Python, not Rails or Node | provisional — but unlikely to move |
| [0002](0002-pgvector-not-a-vector-database.md) | Postgres + pgvector, not a dedicated vector DB | active |
| [0003](0003-fastapi-not-django.md) | FastAPI, not Django or Flask | provisional |
| [0004](0004-arq-not-celery.md) | arq, not Celery | provisional — weakest of the set |
| [0005](0005-hybrid-retrieval-with-rrf.md) | Hybrid retrieval fused with RRF, not pure vector | active |
| [0006](0006-self-hosted-faster-whisper.md) | Self-hosted faster-whisper | superseded by 0013 |
| [0007](0007-expo-managed-not-native-swift.md) | Expo managed workflow, not native Swift | provisional |
| [0008](0008-one-primitive-many-views.md) | One capture primitive, derivations as views | load-bearing |
| [0009](0009-uv-and-src-layout.md) | uv, `src/` layout, Python 3.12 pinned | active |
| [0010](0010-multi-tenant-schema-single-user-product.md) | Multi-tenant schema under a single-user product | active |
| [0011](0011-uuidv7-primary-keys.md) | uuidv7 primary keys, generated in the application | active |
| [0012](0012-job-status-on-the-capture-row.md) | Job status on the capture row, Redis for arq only | active |
| [0013](0013-hosted-stt-not-self-hosted-whisper.md) | Hosted STT (Deepgram Nova-3), not self-hosted Whisper | active |
| [0014](0014-claude-opus-5-for-extraction.md) | `claude-opus-5` for extraction | active |
| [0015](0015-structured-outputs-and-terminal-validation.md) | Native structured outputs; validation terminal after one repair | active |
| [0016](0016-voyage-4-embeddings.md) | `voyage-4` at 1024 dims, model recorded on every row | active |
| [0017](0017-length-dependent-chunking.md) | Length-dependent chunking | active |
| [0018](0018-static-bearer-token-for-v1.md) | Static bearer token gates V1; identity from a dependency | superseded by 0019 |
| [0019](0019-sign-in-with-apple-and-server-sessions.md) | Sign in with Apple; server-issued sessions, hashed | active |
| [0020](0020-morning-view-pins-the-idea-reads-items-live.md) | Morning view pins the idea, reads todos and events live | active |
| [0021](0021-ask-reads-derivations-live.md) | Ask reads retrieved notes' derivations live; knows today | active |
| [0022](0022-deleting-a-note-keeps-its-derivations.md) | Deleting a note keeps its derivations; citations cascade | superseded by 0053 |
| [0023](0023-no-daily-morning-push.md) | No daily morning push; push waits for V4's quality bar | superseded by 0047 |
| [0024](0024-photos-become-text-then-flow-through.md) | A photo becomes text, then flows through the pipeline | active |
| [0025](0025-voice-conversation-own-pipeline-in-two-steps.md) | Voice conversation: our own pipeline, in two steps | active |
| [0026](0026-profile-name-voice-and-account-deletion.md) | Profile: assistant's name for you, voice, account deletion | active |
| [0027](0027-reaching-memo-from-outside-the-app.md) | Outside the app: memo:// links, a share extension, widgets | active |
| [0028](0028-search-related-review-offline-siri.md) | Meaning search, related notes, weekly review, offline outbox, Siri | active |
| [0029](0029-people-last-contact-and-reach-out.md) | People: mentions, deterministic resolution that asks, last contact, reach-out | active |
| [0030](0030-auto-accept-is-not-confirmation.md) | Auto-accept keeps extractions as `accepted`, never `confirmed` | active |
| [0031](0031-themes-are-data-derived-with-a-contrast-guard.md) | Themes are data: two hand-tuned Forest palettes, the rest derived behind a contrast check | active; custom palette removed (0052) |
| [0032](0032-motion-means-something.md) | Motion: Reanimated 4, a vocabulary paired with the haptics | active |
| [0033](0033-native-stack-navigation.md) | React Navigation's native stack, not Expo Router | active |
| [0034](0034-memo-on-the-phone-calendar-and-reminders.md) | Calendar and Reminders: Settings switches that sync, memo → phone only | active; Apple Health read is the one exception (0050) |
| [0035](0035-face-id-lock.md) | Face ID lock: a cover over everything, a minute away | active |
| [0036](0036-event-reminders-are-local-notifications.md) | Event reminders are local notifications, scheduled by reconciling | active |
| [0037](0037-ota-updates-with-a-fingerprint.md) | Over-the-air updates keyed to the native fingerprint | active |
| [0038](0038-recording-keeps-going-in-the-background.md) | Recording keeps going with the phone locked, as a Live Activity | active |
| [0039](0039-word-timings-on-the-capture.md) | Word timings live on the capture | active |
| [0040](0040-on-this-day.md) | On this day: one note from this date in another year, read live | active |
| [0041](0041-easter-eggs-are-rare-and-earned.md) | Easter eggs: rare, earned, never a score | active |
| [0042](0042-faith-a-space-of-its-own.md) | Faith: a space of its own, off until turned on; prayers answered, not done; a plan by position (plan superseded by 0044) | active |
| [0043](0043-habits-noticed-not-logged.md) | Habits: noticed in your notes, suggested when they repeat, counted by rhythm, never a streak | active; amended by 0050; its switch superseded by 0054 |
| [0044](0044-readings-you-choose.md) | Readings you choose: set what you're reading, log what you read, no fixed plan (supersedes 0042's plan) | active |
| [0045](0045-ask-reads-dates-and-people-before-it-searches.md) | Ask reads dates and people from the records before it searches; names go to Deepgram as keyterms | active |
| [0047](0047-a-morning-push-only-when-the-day-has-something.md) | A morning push, only when the day has something in it: once a day, signed-in phones only, measured from the first send (supersedes 0023) | active |
| [0048](0048-a-later-note-can-answer-a-prayer-or-carry-on-an-idea.md) | A later note can answer a prayer or carry on an idea: candidates cheaply, a model decides, the author says yes | active |
| [0049](0049-a-map-of-one-thing.md) | A map of one thing (an ego-graph over existing links), not a map of everything | active on the server; out of the app (0052) |
| [0050](0050-health-milestones-and-apple-health.md) | Health as milestones noticed from notes and Apple Health, never a training log | active |
| [0051](0051-live-words-while-recording.md) | Live words while a voice note records: display only, over Ask's socket, with a patched expo-audio | active |
| [0052](0052-memos-own-dialogs-and-the-places-circle.md) | memo's own dialogs, one Places circle with new-item marks, notes open once saved | active |
| [0053](0053-deleting-a-note-deletes-what-came-from-it.md) | Deleting a note deletes everything taken from it, enforced by a cascade | active |
| [0054](0054-habits-is-always-on.md) | Habits is always on; the column stays as an operator's switch | active |
| [0055](0055-deleting-a-person.md) | Deleting a person forgets them and their mentions; notes and prayers stay | active |
| [0056](0056-a-photo-with-your-words.md) | A photo carries your words: they come first, the description after | active |

Only **0008** is genuinely hard to reverse. **0010** is the next-hardest — not because the
DDL is expensive, but because reversing it means editing the retrieval path, where mistakes
are silent. **0016** is third: changing the embedding model is a full-corpus re-embed, which
is why the model name is on every row. Everything else is a swap with a known cost, and each
ADR names that cost. Decisions we haven't made yet live in
[../open-questions.md](../open-questions.md).
