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
- **Decisions**: ADRs 0001–0020 in [decisions/](decisions/). 0006 and 0018 are superseded.

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
3. **Edit and add derivations from the app.** The server has `PATCH /v1/derivations/{id}`
   with a payload and `POST /v1/captures/{id}/derivations`; the app only confirms or rejects.
   The "the extractor missed one" path is the recall signal the evals need.
4. **Arm the extraction gate with real data.** Record a live run
   (`uv run python -m evals.extraction.run --record evals/extraction/recorded.json`) so CI
   measures Opus against the golden set instead of a replay. Replace the synthetic golden
   cases with real captures as they accumulate. Watch for the leaked-JSON case that the
   validator now catches.
5. **First PITR restore drill.** Dashboard only: Postgres → Backups → restore to a sibling,
   then `scripts/restore_drill.sh` against it, and write the RTO into
   [runbooks/backups-and-restore.md](runbooks/backups-and-restore.md). Nobody has done it.
6. **Ask should see what you confirmed.** Retrieval works over chunk text; a confirmed
   event with a resolved date is invisible to it, which is why an answer hedged on "next
   Tuesday" while extraction had committed to 6 October. Options: embed confirmed
   derivations as their own chunks (the dual-granularity row 0017 already allows), or
   inject today's derivations into the answer prompt. Needs the comparison treatment.
7. **Housekeeping.** Remove the stale `API_TOKEN` variable from both Railway services.
   Export and delete-everything are server-only; give them a home in Settings.

## Rules that still apply

- Every non-trivial choice gets the comparison treatment and an ADR with an interview angle.
- One commit per issue, PR to `main`, merge only on green CI.
- Prompts are code; `extractor_version` is stamped on rows. Changing a prompt is a new version.
- `.env` is never committed; provider keys go to Railway with `scripts/push_secrets.sh`.
- Interactive steps (Apple login, device registration) run in Akin's terminal tab, not in a
  sandboxed shell.
