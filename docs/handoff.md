# Where memo is, and what comes next

_Last updated 2026-09-29. Rewrite this file whenever the answer to "what's next" changes._

## State

V1 is built, merged to `main`, deployed, and installed on Akin's iPhone.

- **Server**: FastAPI + Postgres/pgvector + Redis + arq on Railway (project `memo`,
  services `api` and `worker`; see [runbooks/deploy-railway.md](runbooks/deploy-railway.md)).
  Capture → transcribe (Deepgram) → extract (claude-opus-5) → embed (voyage-4 via the
  Atlas endpoint) → ready. Hybrid retrieval with RRF, SSE ask with citations, morning view,
  rate limits, export/delete, Sign in with Apple with server-issued sessions (ADR 0019).
- **App**: Expo in `apps/mobile`, built with EAS as an ad-hoc `preview` build (see
  [runbooks/mobile-release-eas.md](runbooks/mobile-release-eas.md)). Home list with search,
  record and write, detail with review, delete, playback, Ask sheet, Settings.
- **Quality gates**: 122 tests against real Postgres, ruff, mypy strict, migrations cycled,
  evals for extraction (recorded), retrieval and faithfulness. CI runs on every PR.
- **Decisions**: ADRs 0001–0048 in [decisions/](decisions/) (0046 is on the parked deploy-on-merge branch). 0006 and 0018 are superseded.

## Not done, in the order it should happen

1. **The learning pass.** _Deferred to the end of the build (Akin, 2026-09-28), together
   with cold quizzes._ The repo exists so Akin learns production Python and AI
   engineering; V1 was built by Claude under an explicit one-time suspension of the
   "Akin writes `src/`" rule. Every teach-back in `docs/notes/` is a Claude draft marked for
   rewrite. Go issue by issue (#6 → #49), read the commit for the issue, rewrite its note in
   your own words, and answer the ADR's interview angle out loud. Resume cold quizzes from
   `docs/interview/quiz-bank.md`.
2. **Morning view in the app.** _App side done:_ a "Today" section heads the Home list
   (hidden while searching or when empty), refetched on foreground since the phone sleeps
   through the 04:00 build. Ships with the next EAS preview build (no OTA updates yet).
   The server snapshot went stale during the day (a todo rejected at 09:00 stayed on
   Today); fixed by [ADR 0020](decisions/0020-morning-view-pins-the-idea-reads-items-live.md):
   the idea is pinned at 04:00, todos and events are read live. Push: decided against a
   daily one ([ADR 0023](decisions/0023-no-daily-morning-push.md)); revisit after two weeks
   of use.
3. **Edit and add derivations from the app.** _Done in the app:_ a card awaiting review
   has Confirm / Edit / Reject, a confirmed or added one is tapped to edit, and a ready note
   has "Add what it missed" (todo, event, or idea). Adds `@react-native-community/datetimepicker`,
   a native module, so it needs a new EAS build. Server fix alongside: a payload-only
   PATCH used to flip `user_created` to `confirmed`, erasing the recall signal; an edit
   now confirms only what was `pending_review`. Still open: nothing reads the signal yet;
   the eval intake from real corrections is part of item 4.
4. **Arm the extraction gate with real data.** _Armed:_ `evals/extraction/recorded.json` is
   a live `claude-opus-5` run, so CI now scores real outputs (todo 0.96/0.92, event 1.00/1.00,
   idea 0.78/0.88 precision/recall). The run found g05 corrupted on every sample (leaked
   JSON, once a runaway to `max_tokens`); root cause and fix in ADR 0015's later finding
   (`extract-v1.1`). Known misses are listed in `evals/extraction/README.md`. Still open:
   replace the synthetic golden cases with real captures (every edit/add from item 3 is one),
   Semantic matcher done (`matches.json`, recorded model verdicts, so CI stays offline);
   `evals.extraction.harvest` turns an app export into candidate cases for Akin to review.
5. **First PITR restore drill.** _Deferred_ to [#57](https://github.com/akinshonibare/memo/issues/57)
   (needs the Railway dashboard; Claude can run the verification half).
6. **Ask should see what you confirmed.** _Done_ ([ADR 0021](decisions/0021-ask-reads-derivations-live.md)):
   `answer-v2` attaches each retrieved note's live derivations (date, review status) and
   states today's date; derivation chunks now carry their dates; rejecting retires a chunk.
   Re-embedding on add/edit is done too (ADR 0021 update). Date-range questions and the
   backfill of dates into older item chunks: done in [ADR 0045](decisions/0045-ask-reads-dates-and-people-before-it-searches.md).
7. **Housekeeping.** _Done:_ `API_TOKEN` removed from both Railway services (2026-09-28).
   Settings has Export (JSON through the share sheet, via `expo-sharing`) and Delete
   everything. Delete-everything was failing for anyone who had used Ask (citation FK,
   [#55](https://github.com/akinshonibare/memo/issues/55)); fixed by deleting
   conversations before captures.

## Since the list

- **Planner** (replaces the Items screen): week strip + day list, events by time, todos
  with checkboxes (`derivations.completed_at`, migration 0009), undated todos under
  Anytime, ideas on their own screen. Items can be added with no note behind them
  (`POST /v1/derivations`). Settings regrouped; the type scale is one step smaller.
- **Photos** ([ADR 0024](decisions/0024-photos-become-text-then-flow-through.md)): + on Home →
  take or choose a photo; Claude vision describes it into text, then the ordinary pipeline.
- **Voice conversation** ([ADR 0025](decisions/0025-voice-conversation-own-pipeline-in-two-steps.md)):
  hold the mic in Ask to talk (step 1), or the headset button for hands-free (step 2, live
  WebSocket + Deepgram endpointing, half-duplex). Open: talk-over barge-in needs a native
  voice-processing module; keyterm prompting for names.

- **People** ([ADR 0029](decisions/0029-people-last-contact-and-reach-out.md)): a people
  stage after extraction reads who each note is about and whether you were in touch; a
  deterministic resolver links a name when exactly one person fits and otherwise asks
  "which one?" (note screen, and Who's who on the People screen). Last contact per person,
  a monthly reach-out rhythm by default (editable, or off), at most three in Today with
  *Spoke to them* / *Not now*. Migration 0012 (`people`, `derivations.person_id`); a cron
  reads older notes 40 at a time. Ask is `answer-v2.3`. No new native modules, but the app
  changes ship with the next EAS build. The reading gate is armed (a live `claude-opus-5`
  recording, 1.00 on a starter set that is too easy: replace it with real notes).
  Deepgram keyterms from people's names and "when did I last see X" from the person:
  done in [ADR 0045](decisions/0045-ask-reads-dates-and-people-before-it-searches.md).

- **Keep without asking** ([ADR 0030](decisions/0030-auto-accept-is-not-confirmation.md)):
  Settings → Review, off by default. When on, new items are kept as `accepted` (not
  `confirmed`): Ask trusts them, the eval harvester never labels them. Migration 0013.

- **A note with no words is not an error** (2026-09-29): a voice note where nothing was
  said used to fail at extraction with a Retry that could never work. The pipeline now
  skips extraction, people and indexing for it (no model calls) and marks it ready; the
  note says "Nothing was said in this recording." Migration 0014 readies the ones that
  had already failed that way, and only those.

- **Review of the people work** (2026-09-29, PRs #69-#71): Ask crashed on any note
  naming a linked person (a `dict(Result)` bug; fixed, and `tests/test_route_sweep.py`
  now calls every route against every kind of row). A failed people read no longer
  fails its note; the backlog doesn't count 429s/5xx as attempts; merges keep one
  mention per person per note; a logged contact can be undone in the app. Migration
  0008's downgrade was broken on real data (fixed; `tests/test_migrations.py`).
  **Open:** Voyage via Atlas is capped at 3 requests/minute until a payment method is
  added in Atlas billing (it stays free under the 200M-token allowance); the app fixes
  in #71 need the next EAS build.

- **Look, feel and the phone** (2026-09-29, ADRs 0031-0041; PR #72, main `8535c6d`;
  server deployed, EAS build 17 submitted to TestFlight):
  - *Themes:* System / Light / Dark, with Forest as the default and a hand-designed light
    Forest, plus Tide, Clay, Plum, Ink and Custom (two hues, contrast-checked).
  - *Motion:* the living full stop, the voice orb, ink-in, fireflies, springy lists, the
    circular theme reveal.
  - *Navigation:* a native stack, so screens push like iOS and swipe back.
  - *Swipes:* swipe left to delete a note (the "…" is gone); swipe right to tick in the
    Planner.
  - *On the phone:* Settings switches for events into a "memo" calendar, todos into a
    "memo" Reminders list (ticking syncs both ways), and event notifications. memo never
    reads your own calendar.
  - *Privacy and recording:* a Face ID lock. Recording keeps going with the phone locked,
    with a Lock Screen / Dynamic Island Live Activity.
  - *Playback:* karaoke follows the transcript as a note plays.
  - *Server:* word timings (migration 0015) and "On this day" in the morning view.
  - *Easter eggs (ADR 0041):* tap the dot (five quick taps: a swarm); search "fireflies",
    "snow", "leaves" or "petals"; seasonal particles; milestone notes (100, 500, 1,000…);
    memo's birthday; "Still up, Akin?" between 1 and 5am.
  - *Reviewed before shipping:* an independent review found 13 issues, all fixed. The
    ones worth knowing: the Face ID lock was a modal and couldn't appear over an open
    sheet (now a cover inside every sheet); phone sync tags every copy with its memo
    item (`memo://item/<id>`) so a lost record never duplicates, only touches a calendar
    or list whose id it recorded (never one merely called "memo"), runs one pass at a
    time, clears removed notes/due dates/locations, and takes memo's copies off the
    phone at sign-out; "On this day" now queries UTC ranges an index can serve.
  - *Updates:* OTA via EAS Update, keyed to a native fingerprint.

  **Shipped** in this order: api (migration 0015 applied) and worker with `railway up`
  from `origin/main`, then EAS production build 17 with `--auto-submit`. Build 16
  failed at "Configure expo-updates": the local Xcode 26.2 patch to
  `node_modules/expo-modules-jsi` changed the native fingerprint, so the runtime version
  computed on the Mac didn't match EAS's. `npm ci` before any EAS build (see the release
  runbook). From build 17 on, JavaScript-only changes can go out with `eas update`.
  **Untested by hand:** the five-tap swarm (the simulator tools are too slow for a
  two-second window); try it on the phone.

## Next up (Akin, 2026-09-29)

1. **Faith**: built and shipped ([ADR 0042](decisions/0042-faith-a-space-of-its-own.md)).
   Off until turned on in Settings → Faith. Prayers (answered, not done, linked to people),
   one thing a day to remember, passages found in notes and opened in YouVersion (NIV by
   default), a Faith section in the weekly review, and a `faith-v1` reader that files
   prayers from any note (its own eval). **Revised the same day**
   ([ADR 0044](decisions/0044-readings-you-choose.md), migration 0018): no fixed plan. You
   set what you're reading ("John"), memo offers the next chapter, "Read" logs it, and
   any passage can be logged; the passages your notes name are listed as a reference.
2. **Habits**: built ([ADR 0043](decisions/0043-habits-noticed-not-logged.md), migration 0017).
   Off until turned on in Settings → Habits. The `habits-v1` reader picks out what each
   note says you did (gym, running, reading, the Bible, prayer, and so on, or a habit you
   named); memo suggests a habit at 3 days in 3 weeks, on Today and the Habits screen.
   Counted by the day: "2 of 3 this week", the last time, eight weeks of bars, and every
   day with the note behind it. "Log today" and removing a day are one tap each. Time
   with family comes from People (a Family switch on each person, guessed from "Mum",
   "brother" and the like); reading the Bible comes from Faith. A gentle nudge on Today
   when a habit with an aim has gone quiet for twice its usual gap. A Habits section in
   the weekly review. The last 60 days of notes are read in the background when it's
   turned on. Gated by its own eval (`evals/habits`, 32 cases, armed at 1.00, replayed
   in CI).
3. **The gap after deleting a note** (Akin's screenshot, build 17): Home's rows were
   moved by Reanimated layout animations, which can be left stranded in a virtualized
   list. Home no longer animates positions ([ADR 0032](decisions/0032-motion-means-something.md),
   "What changed"). Not reproducible in a debug build, so confirm on the phone.
4. **Pull to refresh** on every list screen (`src/refresh.tsx`).
5. **The five-tap dot "doesn't work"** (2026-09-29): not a bug. Akin's phone had Reduce
   Motion on, which hides every particle and the dot's hop by design (ADR 0032). The
   whole path was checked in the simulator: five taps, the burst, autumn's leaves.

## The next round (agreed with Akin, 2026-09-29)

Asked "what are we missing?", the gaps against the brief's roadmap were listed and Akin
picked these, one PR each, in this order:

1. **The gaps in Ask and People** ([ADR 0045](decisions/0045-ask-reads-dates-and-people-before-it-searches.md)).
   _Built:_ Ask reads the days a question names ("this week", "last Friday", "in
   March") and pins the items dated in them; "when did I last see X" comes from the
   person's recorded contact; retrieval searches inside those days first; people's names
   go to Deepgram as keyterms (voice notes, hold-to-talk, live voice); an hourly sweep
   rewrites item chunks from before dates were in their text. `answer-v2.4`. New gate:
   `evals/timewindow` (41/45, zero wrong windows). _Still open:_ the extraction golden
   set is still Claude-written; `evals.extraction.harvest` can turn Akin's reviewed notes
   into cases, but his own text has to be read by him before it lands in the repo.
2. **Push** ([ADR 0047](decisions/0047-a-morning-push-only-when-the-day-has-something.md),
   supersedes 0023; migration 0019). _Built:_ Settings → Morning: a switch and a time.
   The server sends today's events and the todos due today, once a day at most, only
   to signed-in phones, only on a day with something in it; 👍/👎 on the notification
   and taps are recorded (`GET /v1/pushes`: act-on rate). The app side is JavaScript
   only (fingerprint `79d9bbf7` = build 17), so it ships as an OTA update. **Needs Akin
   once:** the APNs key via `eas credentials` (runbook: mobile-release-eas.md, "Push
   notifications"). Checked in the simulator: the Settings row, the timeout message,
   a push's tap and 👎 reaching the server. Not checkable there: a real token.
   Event-driven nudges (V4) are the follow-up that reuses this.
3. **A live transcript while recording a note** (V1.5): the Ask WebSocket path, applied
   to capture.
4. **A new note answers a prayer or carries on an idea** ([ADR 0048](decisions/0048-a-later-note-can-answer-a-prayer-or-carry-on-an-idea.md),
   migration 0020). _Built (done before 3, at Akin's ask):_ a `reconcile` stage after
   embed compares a note with open prayers (Faith on, answer-ish words) and nearby
   ideas; `reconcile-v1` decides, quoting the note; Faith asks "Answered?", Ideas asks
   "Carries on an idea?", and a yes answers the prayer (the note's day, its words) or
   adds to the idea's thread. `evals/reconcile` (26 cases, 1.00/1.00 on a starter set;
   real cases come from Akin's yes/no). JavaScript-only on the app (fingerprint
   `79d9bbf7`), so an OTA update.
5. **Health**: light workout milestones noticed without logging ("first 5k", "heaviest
   squat yet"). **Akin said yes to reading Apple Health** (workouts, steps, sleep): the
   first exception to memo → phone only. Still a Settings switch.

**Builds (2026-09-29):** the free EAS iOS build quota is used up for the month. Items
2-4 and Health-from-notes are JavaScript and ship as OTA updates to build 17; only the
Apple Health read needs a new build (a HealthKit module and entitlement). Batch every
native change into that one build, when the quota resets or from a local build (which
needs the Xcode 26.2 `expo-modules-jsi` patch committed with patch-package first, or the
fingerprint won't match later updates).

6. **A map of memo** (Akin, 2026-09-29, asked for next): a mind-map view over what
   memo already links: people and the notes about them, prayers and who they're for,
   ideas and their threads (ADR 0048), passages, related notes. To be designed.

Not now: **Sentry** (Akin, 2026-09-29). Still Akin's to do: the PITR drill (#57), a
payment method on Voyage/Atlas (3 requests a minute until then).

## Filed for later

- [#57](https://github.com/akinshonibare/memo/issues/57): the first PITR restore drill.
- ~~#55, #56~~: done in ADR 0022 (deleting a note keeps its derivations; citations cascade).

## Rules that still apply

- Every non-trivial choice gets the comparison treatment and an ADR with an interview angle.
- One commit per issue, PR to `main`, merge only on green CI.
- Prompts are code; `extractor_version` is stamped on rows. Changing a prompt is a new version.
- `.env` is never committed; provider keys go to Railway with `scripts/push_secrets.sh`.
- Interactive steps (Apple login, device registration) run in Akin's terminal tab, not in a
  sandboxed shell.
