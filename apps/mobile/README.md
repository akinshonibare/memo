# memo — mobile client

Expo (managed workflow, ADR 0007), TypeScript, no navigation library: four tabs in
`App.tsx`. Three screens from the brief plus settings for the API URL and token.

| Screen | Issue | What it does |
| --- | --- | --- |
| Capture | #43 | Text field + hold-to-record. Optimistic row, then the server's id. Audio goes to the bucket via a presigned PUT; the API never sees the bytes. |
| Review | #44 | Polls capture status; confirm/reject derivations in one tap; failed captures show the reason and a retry. |
| Ask | #45 | Create-then-stream over SSE. Citations render from the `retrieval` event before the first token; tapping one opens the review tab. |
| Settings | — | API base URL and the static bearer token (ADR 0018), in the device's secure storage. |

```bash
cd apps/mobile
npm install
npx expo start --ios      # simulator with Expo Go
```

Point Settings at `http://<your-mac-ip>:8000` for a local API (the simulator can use
`http://localhost:8000`), or at the Railway domain in production. Recording needs a
real device or the simulator's microphone permission.
