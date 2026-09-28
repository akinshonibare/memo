# 0025 — Voice conversation with Ask: our own pipeline, built in two steps

**Status:** active (both steps built)
**Date:** 2026-09-28

## Problem

Akin wants to talk to memo and hear it answer, not only type. Ask is worth talking to
because of what's behind it: hybrid retrieval, live derivations (ADR 0021), citations,
and conversations saved with the notes they were grounded in. A voice mode has to keep
all of that; it's another way in, not a different assistant.

Deepgram was already here for transcribing voice notes (ADR 0013). That was a batch
job over a stored recording, which is not the same thing as a conversation.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Deepgram Voice Agent API** (one WebSocket: speech-to-text, the LLM, and text-to-speech) | Fastest to a demo. Endpointing and barge-in come built in | $0.075/min. The *conversation loop* runs at Deepgram, so retrieval becomes a function call back into us, and citations and saved history have to be rebuilt around its transcript. LLM support for Claude is unclear. Lock-in on the core interaction |
| **Turn-based walkie-talkie**: hold to talk, upload the clip, transcribe, the existing Ask flow, then speak the answer | Reuses everything; no new transport. Days of work | Seconds before anything is heard. No interrupting mid-answer except by hand |
| **Our own streaming pipeline**: the phone streams mic PCM over a WebSocket, Deepgram live speech-to-text with endpointing, the existing Ask flow, the answer spoken sentence by sentence, barge-in cancels generation and playback | Natural turn-taking, ~2 s to first audio, and the same conversations and citations as typed Ask. The hard, interesting engineering (the brief's V1.5) | The most code: a WebSocket endpoint, cancellation across three streams, audio chunk playback. A few cents per conversation |
| **On-device**: iOS speech recognition plus iOS speech synthesis | Free, private, no server work | Robotic voice, and weaker on proper nouns, which is the reason ADR 0013 chose Deepgram |

## Decision

**Build our own pipeline in two steps.** Step 1 is the walkie-talkie with a spoken
answer. Step 2 replaces its transport with live streaming and automatic turn-taking. The
conversation logic stays the existing Ask flow throughout.

**Step 1 (built):**
- `POST /v1/voice/transcribe` takes the clip's bytes and returns text (Nova-3, prerecorded).
  The clip is never stored: a spoken question is a way of typing, not a note.
- The text goes through the ordinary `POST /conversations/{id}/messages` and SSE stream.
- The app splits the streamed answer into sentences as they arrive and speaks each with
  `POST /v1/voice/speak` (Aura-2, same Deepgram key). The next sentence's audio is
  fetched while the current one plays. Citation markers and markdown are stripped before
  speaking.
- Holding the mic or tapping the speaker icon stops the answer. That's the manual version
  of barge-in.
- Rate-limited, fail-closed (`rate_voice_per_minute`): every call is a paid one.

**Step 2 (built): hands-free, half-duplex.**
- `useAudioStream()` (expo-audio, SDK 57) streams 16-bit PCM over a `/v1/voice/live`
  WebSocket. It is authenticated with the same Bearer header as HTTP, never a token in
  the URL. The first message carries the mic's real sample rate.
- The server relays the audio to Deepgram live (Nova-3, `endpointing=400`,
  `utterance_end_ms=1200`, VAD events) and reduces the results to `partial`, `final` and
  `speech_started`. It sends a KeepAlive during silence, always closes the paid upstream
  socket, and logs audio seconds and cost at close.
- The app asks each `final` through the ordinary flow and speaks the answer.
  **Half-duplex:** the mic is muted in software while the answer plays, then listening
  resumes. A tap interrupts: speech stops, generation is cancelled, and it listens again.
- Measured on real audio: `speech_started` at 0.4 s, and `final` ~0.7 s after the speaker
  stopped.

**Why half-duplex, not talk-over barge-in:** expo-audio exposes no acoustic echo
cancellation. On the phone's loudspeaker the mic hears memo's own answer, so
`speech_started` would fire on memo's voice and it would interrupt itself. True barge-in
needs iOS voice processing (`AVAudioEngine` input with voice processing enabled), which
means a native Expo module.

## What we're accepting

- **Step 1 latency is measured, not hoped.** An Aura-2 REST call for one sentence took
  ~1.8 s. First audio is therefore the answer's time to first sentence plus ~1.8 s. Step 2
  should use Deepgram's streaming TTS (WebSocket) or an early short sentence to beat it.
- **Step 1 moves question audio through the API**, unlike recordings, whose bytes go
  straight to the bucket. A question is seconds long, capped at 5 MB, and never stored,
  so the presign round trip would only add latency.
- **Two transports for a while.** Step 1's endpoints stay as the fallback when the socket
  can't connect.
- **Cost is logged, not metered in `llm_calls`.** Speech is priced per character, not per
  token. The `spoken` log line carries characters and dollars; if voice becomes a real
  share of spend, it gets its own usage table.

## Revisit when

- Interrupting by tapping feels wrong in practice: write the local Expo module that
  enables iOS voice processing, then treat `speech_started` during an answer as barge-in.
- Proper nouns are misheard: the live test heard "Okafor" as "Akhafur". Nova-3 supports
  keyterm prompting, and the names already in the user's notes and derivations are the
  obvious keyterms to send when a session opens.
- A voice conversation needs to *act* (add a todo, tick one off): that's tool use in the
  answer model, and the first time Ask writes rather than reads. It needs its own ADR.
- Spend: if voice minutes grow, compare against the Voice Agent API's flat $0.075/min
  with real numbers.

## Interview angle

> **"Add a voice interface to your RAG assistant."**

The trap is buying a voice agent and discovering it owns your conversation loop:
retrieval becomes a tool call, and your citations and history live in someone else's
transcript. I keep the loop and treat voice as transport. Speech-to-text turns speech
into the same message a typed question is, and text-to-speech turns the streamed answer
into audio a sentence at a time, so the first sentence plays while the model is still
writing. I shipped it in two steps: turn-based first, then live streaming with endpointing
and barge-in, which is where the real engineering is (cancelling generation, playback and
the audio stream together, without leaking any of them). The costs I'd name: two
transports during the migration, and time-to-first-audio is dominated by TTS latency,
which I measured at ~1.8 s per sentence over REST.
