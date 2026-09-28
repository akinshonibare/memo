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
- **Decisions**: ADRs 0001–0021 in [decisions/](decisions/). 0006 and 0018 are superseded.

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
   the idea is pinned at 04:00, todos and events are read live. Then decide whether a push
   notification is worth an APNs setup.
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
   and a semantic title matcher for paraphrased ideas.
5. **First PITR restore drill.** Dashboard only: Postgres → Backups → restore to a sibling,
   then `scripts/restore_drill.sh` against it, and write the RTO into
   [runbooks/backups-and-restore.md](runbooks/backups-and-restore.md). Nobody has done it.
6. **Ask should see what you confirmed.** _Done_ ([ADR 0021](decisions/0021-ask-reads-derivations-live.md)):
   `answer-v2` attaches each retrieved note's live derivations (date, review status) and
   states today's date; derivation chunks now carry their dates; rejecting retires a chunk.
   Not done: re-embedding on edit/add, and date-range questions ("what's this week?").
7. **Housekeeping.** _Done:_ `API_TOKEN` removed from both Railway services (2026-09-28).
   Settings has Export (JSON through the share sheet, via `expo-sharing`) and Delete
   everything. Delete-everything was failing for anyone who had used Ask (citation FK,
   [#55](https://github.com/akinshonibare/memo/issues/55)); fixed by deleting
   conversations before captures. Single-note delete still hits #55.

## Filed for later

- [#55](https://github.com/akinshonibare/memo/issues/55): deleting a note that an answer
  cited fails with an FK violation.
- [#56](https://github.com/akinshonibare/memo/issues/56): deleting a note should keep its
  extracted todos, events and ideas. Do with #55; one ADR.

## Rules that still apply

- Every non-trivial choice gets the comparison treatment and an ADR with an interview angle.
- One commit per issue, PR to `main`, merge only on green CI.
- Prompts are code; `extractor_version` is stamped on rows. Changing a prompt is a new version.
- `.env` is never committed; provider keys go to Railway with `scripts/push_secrets.sh`.
- Interactive steps (Apple login, device registration) run in Akin's terminal tab, not in a
  sandboxed shell.
