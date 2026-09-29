# Where memo is, and what comes next

_Last updated 2026-09-28. Rewrite this file whenever the answer to "what's next" changes._

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
- **Decisions**: ADRs 0001–0025 in [decisions/](decisions/). 0006 and 0018 are superseded.

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
   Re-embedding on add/edit is done too (ADR 0021 update). Not done: date-range questions
   ("what's this week?"), and backfilling dates into pre-existing derivation chunks.
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
  **Open:** Deepgram keyterms from people's names; Ask answering "when did I last see X"
  from the person rather than retrieval.

- **Keep without asking** ([ADR 0030](decisions/0030-auto-accept-is-not-confirmation.md)):
  Settings → Review, off by default. When on, new items are kept as `accepted` (not
  `confirmed`): Ask trusts them, the eval harvester never labels them. Migration 0013.

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
