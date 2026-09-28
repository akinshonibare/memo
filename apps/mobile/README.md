# memo — mobile client

Expo (managed workflow, ADR 0007), TypeScript, no navigation library: `App.tsx` holds a
home/detail view and four sheets.

| Screen | Issue | What it does |
| --- | --- | --- |
| Home | #43 | Every capture, newest first, with search. The red button starts a voice note; the pencil opens a text note. "Ask anything" opens the Ask sheet. |
| Record sheet | #43 | Opens already recording: Pause/Resume, timer, End. Audio goes to the bucket via a presigned PUT; the API never sees the bytes. Closing discards. |
| Detail | #44 | The capture, its processing state (polled while active), derivations to confirm or reject in one tap, and a failure reason with Retry. |
| Ask sheet | #45 | Create-then-stream over SSE. Citations arrive in the `retrieval` event before the first token and sit behind a Sources toggle; tapping one opens that capture. The sheet stays mounted so the conversation survives closing it; `+` starts a new one. |
| Settings sheet | — | API base URL and the static bearer token (ADR 0018), in the device's secure storage. Shown on first launch until both are set. |

Look and feel: pure black, Instrument Serif for display text, the system sans for
everything else, pills instead of rectangles. Colours and type live in `src/ui.ts`.

```bash
cd apps/mobile
npm install
npx expo start --ios      # simulator with Expo Go
```

Point Settings at `http://<your-mac-ip>:8000` for a local API (the simulator can use
`http://localhost:8000`), or at the Railway domain in production. Recording needs a
real device or the simulator's microphone permission.
