# 0013 — Transcription runs at a hosted STT provider (Deepgram Nova-3)

**Status:** provisional — becomes `active` when [#16](https://github.com/akinshonibare/memo/issues/16) lands
**Date:** 2026-09-27
**Supersedes:** [0006](0006-self-hosted-faster-whisper.md)
**Closes:** Q9, [#14](https://github.com/akinshonibare/memo/issues/14)

## What changed

0006 chose self-hosted `faster-whisper` on one argument: audio never leaves the machine. Q8
removed the machine. There is no homelab; everything runs on Railway, so the audio sits on a
cloud provider's disks either way. The premise did not turn out to be wrong — it was
**overtaken**: 0006's reasoning was correct for the topology it was written against, and that
topology no longer exists.

With privacy no longer decisive, the axis Q9 named takes over: **STT accuracy on proper
nouns** (which feeds retrieval quality — see [0005](0005-hybrid-retrieval-with-rrf.md), where
names are exactly what the lexical branch exists to catch) against **how much of the V1
learning budget goes to model-serving ops** instead of extraction, retrieval, and streaming.

## Problem

A voice capture has to become searchable text without touching the request path. Volume is
~20 captures a day, mostly under a minute. The transcript is the *only* copy of what was said
that the rest of the system ever sees — a name mangled here is a name retrieval will never
find.

## Options

Prices verified 2026-09-27; re-check before quoting.

| Option | What it buys | What it costs |
| --- | --- | --- |
| **`faster-whisper` on the Railway worker (CPU)** | No vendor, no key, audio never leaves Railway. The industry's default open model. What 0006 would have become. | A ~500 MB model baked into the image or fetched onto a volume at first boot; CPU inference at 1–2× realtime on a shared container; cold start on the first job of the day. Whisper-small is measurably weaker than current hosted models on rare names. And every hour on model serving is an hour not spent on the parts of this build that are actually the point. |
| **`faster-whisper` on serverless GPU** (Modal / Replicate / RunPod) | Large-model accuracy without a resident GPU. | A fourth platform in the deploy story, cold starts measured in tens of seconds, per-second billing that is fine at this volume and a new failure mode (the GPU platform is down) with nothing to show for it at V1 scale. |
| **Hosted STT API** — AssemblyAI $0.0025/min · OpenAI `gpt-4o-mini-transcribe` $0.003 · **Deepgram Nova-3** $0.0043 batch · OpenAI `gpt-4o-transcribe` $0.006 | Best accuracy on names available. Zero ops. At 20 one-minute captures a day, **under $4/month** on any of them. Deepgram's prerecorded API accepts keyterm boosting, which is a direct lever on the proper-noun problem: confirmed names from derivations can be fed back in later. | Audio leaves Railway for a third vendor. One more account, one more key, one more thing that can be down. The transcript's quality is now a function of someone else's model roadmap. |

## Decision

**Deepgram Nova-3 via the prerecorded (batch) API, behind a `Transcriber` protocol.** The
provider-specific code is one class; a `FakeTranscriber` serves tests; `faster-whisper` is a
second class away if the reasons for hosted stop holding.

Deepgram over the cheaper options because of keyterm boosting — the one feature in the table
that speaks to the axis this decision is actually about. Over `gpt-4o-transcribe` because it
is cheaper and adds a vendor with a narrower blast radius than an OpenAI account.

## What we're accepting

**Audio leaves the platform.** 0006's privacy argument is now formally dead, not merely
weakened. For a personal journal that is a real thing to have given up, and the honest framing
is that it was given up at Q8, not here.

**A third vendor and key** (`DEEPGRAM_API_KEY`) alongside Anthropic and Voyage. Three
inference bills for one user.

**Dependence on a hosted model's behaviour.** A silent model update upstream can change
transcript quality with no diff in this repo. The mitigation is the same one everything else
here relies on: an eval. A small set of recordings with known transcripts, scored on the names
they contain, is the transcription equivalent of the extraction golden set — deferred, and
named here so it is not forgotten.

## Revisit when

- Monthly STT spend exceeds ~$20 — at that volume the ops cost of self-hosting starts to pay
  for itself and the calculus in the table flips.
- A transcript eval shows name accuracy below what retrieval needs, and keyterm boosting
  doesn't close the gap — then a larger self-hosted model on GPU is the next move.
- V1.5 streaming voice arrives. Streaming STT is a different product with different pricing
  and this ADR does not cover it.

## Interview angle

> **"You had an ADR that said self-host Whisper. Why is transcription now a vendor call?"**

Because the premise it stood on stopped being true, and I'd rather say that than defend it.
The self-hosting argument was privacy — audio never leaves the machine. When the deploy
topology moved entirely to a cloud host, the audio was on someone else's disk regardless, so
privacy stopped being the deciding axis. What was left was accuracy on proper nouns versus
ops burden, and at twenty captures a day a hosted model wins both: it is better on names,
which is what my retrieval quality depends on, and it costs a few dollars a month against
hours of model-serving work that teaches me nothing I came here to learn. The thing I'd
volunteer: I kept the old ADR and marked it superseded, because the difference between "I
was wrong" and "the world moved" is the whole point of writing these down — and this one was
the world moving.
