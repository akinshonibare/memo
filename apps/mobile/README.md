# memo — mobile client

Expo (managed workflow, ADR 0007), TypeScript, no navigation library: `App.tsx` holds a
home/detail view and four sheets.

| Screen | Issue | What it does |
| --- | --- | --- |
| Home | #43 | Every capture, newest first, with search. The red button starts a voice note; the pencil opens a text note. "Ask anything" opens the Ask sheet. |
| Record sheet | #43 | Opens already recording: Pause/Resume, timer, End. Audio goes to the bucket via a presigned PUT; the API never sees the bytes. Closing discards. |
| Detail | #44 | The capture, its processing state (polled while active), derivations to confirm or reject in one tap, and a failure reason with Retry. |
| Ask sheet | #45 | Create-then-stream over SSE. Citations arrive in the `retrieval` event before the first token and sit behind a Sources toggle; tapping one opens that capture. The sheet stays mounted so the conversation survives closing it; `+` starts a new one. |
| Sign in | — | Sign in with Apple (ADR 0019). The server exchanges Apple's identity token for a session that lives in the Keychain; nothing to copy or paste. |
| Settings sheet | — | Timezone (pushed to the server), the API base URL (defaults to production), and Sign out, which revokes this device's session only. |

Look and feel: pure black, Instrument Serif for display text, the system sans for
everything else, pills instead of rectangles. Colours and type live in `src/ui.ts`.

```bash
cd apps/mobile
npm install
npx expo start --ios      # simulator with Expo Go
```

Sign in with Apple needs a development or preview build (`eas build --profile preview`);
Expo Go can't present the Apple sheet. Point Settings at `http://localhost:8000` for a
local API. Recording needs a real device or the simulator's microphone permission.
