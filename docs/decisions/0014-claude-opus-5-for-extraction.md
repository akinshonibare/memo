# 0014 — `claude-opus-5` for extraction

**Status:** provisional — becomes `active` when [#23](https://github.com/akinshonibare/memo/issues/23) lands
**Date:** 2026-09-27
**Closes:** Q1, [#20](https://github.com/akinshonibare/memo/issues/20)

## Problem

Extraction is the correctness-critical path. [0008](0008-one-primitive-many-views.md) moved
every schema guarantee into the extractor: if it misses a todo, there is no typed form the
user filled in as a fallback — the todo does not exist. NFR4 turns that into a number (todo
recall ≥ 0.90, precision ≥ 0.80) and a CI gate.

So the choice is which model sits at the point where the app is most likely to be wrong, at
~20 captures a day, each a few hundred tokens of informal text.

## Options

Prices per million tokens, first-party API, verified 2026-09-27. Assume ~1,500 tokens per
capture round-trip (system prompt + text + structured output) — ~30k tokens/day.

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Claude Haiku 4.5** — $1 / $5 | Cheapest and fastest. ~$1.20/month. The obvious pick for "extract three fields from a short note". | Weakest on the cases that matter: negation ("don't forget I *already* cancelled the dentist"), relative dates, an idea phrased as an imperative. Those are exactly the golden-set hard cases. Still uses `budget_tokens`-style thinking, an older API surface. |
| **Claude Sonnet 5** — $2 / $10 | ~$2.50/month. The default production tier for most extraction workloads. | Middle of the table on both axes; no argument for it that isn't "compromise". |
| **Claude Opus 5** — $5 / $25 | ~$6/month. The strongest reasoning available at this tier, adaptive thinking on by default, structured outputs with `output_config.format`. The model most likely to clear NFR4 on the first attempt, which matters because a failing gate blocks every other PR. | 5× Haiku per token, for a task a smaller model *might* do fine. Slower per call — irrelevant in a background job. |
| **Small local model for the easy majority, escalate the rest** ([#20](https://github.com/akinshonibare/memo/issues/20)'s suggestion) | Near-zero marginal cost, no vendor for 80% of captures. | Two extractors means two prompts, two `extractor_version`s, two eval numbers, and a classifier deciding which is which — a third thing to be wrong. Also a model to serve on Railway, which [0013](0013-hosted-stt-not-self-hosted-whisper.md) just declined to do for STT on the same grounds. |

## Decision

**`claude-opus-5`.** The strongest available model at the point where the application is
most exposed, because at this volume the entire monthly cost difference is a coffee and the
cost of a missed todo is trust.

The escalation design is not wrong — it is early. It needs the golden set to exist first, so
there is a number to say which captures are "easy".

## What we're accepting

**Paying 5× per token for capacity we may not need.** Until [#24](https://github.com/akinshonibare/memo/issues/24)
and [#25](https://github.com/akinshonibare/memo/issues/25) exist we have no evidence Haiku
would fail the gate. This is quality-first by assumption, and the assumption gets tested the
day the eval runner works.

**Vendor lock-in at the most important call site.** The prompt and the structured-output
mechanism ([0015](0015-structured-outputs-and-terminal-validation.md)) are Anthropic-shaped.
Swapping providers is a rewrite of `services/extraction.py`, not a config change.

**A thinking model on a job that mostly doesn't need to think.** Adaptive thinking is on by
default; `output_config.effort` is set to `low` for extraction so the model doesn't spend
tokens deliberating over "buy milk".

## Revisit when

- The golden set shows Haiku 4.5 (or Sonnet 5) within 2 points of Opus on todo recall — then
  drop down, because the quality argument has been measured and lost.
- Monthly extraction spend passes ~$30, which means volume has changed shape and the
  escalation design is worth its complexity.
- Anthropic ships a model that changes the price/quality curve. Re-run the table; don't
  inherit this choice.

## Interview angle

> **"Which model did you use for extraction and how did you choose?"**

I'd say what the decision was *for* first: in this architecture extraction correctness is
application correctness, because there's no typed schema catching mistakes behind it. So the
question was "which model at the point of maximum exposure", and the answer was the strongest
one — at twenty captures a day the cost difference between the cheapest and the most capable
model is a few dollars a month. What makes that a decision rather than a reflex is that it's
falsifiable: the eval set measures todo recall per model, and the ADR says which number would
make me switch down. The version of this answer that sounds naive is "we used the best model";
the version that sounds senior is "we used the best model *and here is the measurement that
would change my mind*."
