# 0006 — Self-hosted faster-whisper, not hosted STT

**Status:** under revision — ⚠️ the homelab is off the table (2026-08-17)
This ADR's core argument was "audio never leaves the machine." Without a homelab there is
no such machine, and the privacy premise collapses. Do not cite this ADR until it is
replaced. Superseding decision pending — see [../open-questions.md](../open-questions.md) Q8.
**Date:** 2026-08-17

## Problem

V1 needs batch transcription: record a voice memo, upload the file, get text back within a
reasonable time. Not streaming. The content is a personal journal that will eventually
include health, faith, and relationship material.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Hosted (Deepgram, AssemblyAI, provider Whisper endpoints)** | Zero ops. Strong accuracy. Real streaming endpoints, diarization, word-level timestamps, punctuation, custom vocabulary. Scales without thought. | Per-minute cost forever. Every recording — including the ones about health and faith — leaves the machine and lands in a third party's retention policy. Network dependency on the critical path. Vendor lock on the streaming API shape. |
| **`openai/whisper` reference implementation** | The canonical model, easy to reason about. | Slow and memory-hungry. Poor batching. Effectively a research implementation, not a serving one. |
| **`faster-whisper` (CTranslate2 reimplementation)** | Roughly 4× faster than the reference at equal accuracy, with int8 quantisation making CPU-only inference genuinely viable and GPU inference fast. Zero marginal cost. Audio never leaves the homelab. Model choice (`tiny`→`large-v3`) is a dial you control. Forces you to learn quantisation, VAD, and model serving — which is on the curriculum. | You own it: model download, warm-up time, memory footprint, GPU driver stack, and the fact that a cold worker's first transcription is slow. Homelab uptime becomes app uptime. Accuracy on accents and proper nouns is worse than the best hosted vendors, and *your* transcripts are full of proper nouns. |

## Decision

faster-whisper, self-hosted, for V1 — because V1 is batch-only (so the hosted streaming
advantage is worth nothing yet), the content is genuinely sensitive, and the marginal cost
is zero for a workload that could otherwise run all day.

## What we're accepting

We accept worse accuracy than a top hosted vendor, particularly on names — which matters
more here than in a generic app, because names are exactly what the retrieval layer needs
to be sharp on (see [0005](0005-hybrid-retrieval-with-rrf.md)). Mitigation available if it
bites: faster-whisper accepts an `initial_prompt`, which can be seeded with a vocabulary of
recurring names pulled from existing derivations.

We accept that homelab downtime means captures queue rather than transcribe. This is
acceptable **only because** the queue is durable (see [0004](0004-arq-not-celery.md)) — the
capture is already safely written before transcription is attempted. Losing a recording is
not on the table; delaying one is.

We accept cold-start latency on the first job after a worker restart.

## Revisit when

- **V1.5 (streaming voice) — mandatory re-open.** Streaming STT is a materially different
  problem: chunked audio, VAD, incremental decoding, partial-hypothesis stability. Hosted
  vendors have solved it; doing it yourself is the hard-engineering milestone. Decide then
  whether that difficulty is the *point* (learning) or a detour (shipping).
- Word error rate on proper nouns is bad enough that retrieval visibly suffers, and
  `initial_prompt` seeding doesn't recover it.

## Interview angle

> **"When do you self-host a model instead of calling an API?"**

Four axes, in roughly this order: **data sensitivity** (does this content have a policy
reason not to leave your perimeter?), **marginal cost at your volume** (per-minute pricing
is irrelevant at 10 minutes/day and decisive at 10,000), **latency shape** (batch is easy
to self-host, low-latency streaming is where hosted vendors have done years of work you
can't replicate in a weekend), and **ops appetite** (self-hosting means you own the GPU
stack, the model lifecycle, and the cold start).

For memo the answer is self-host, but notice that the *reason* is privacy plus zero
marginal cost — not accuracy, where hosted wins. Being able to say which axis you lost on
is the difference between having made a decision and having had a preference.
