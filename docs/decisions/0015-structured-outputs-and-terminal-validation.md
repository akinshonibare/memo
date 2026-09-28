# 0015 — Structured outputs via the API's schema constraint; semantic validation failures are terminal after one repair

**Status:** active — since [#23](https://github.com/akinshonibare/memo/issues/23) (2026-09-28)
**Date:** 2026-09-27
**Closes:** Q2, [#21](https://github.com/akinshonibare/memo/issues/21)

## Problem

The extractor has to return typed derivations — a todo with a title and an optional due date,
an event with a start time — and the rest of the system trusts those types. Two questions,
and the second is the interesting one:

1. How is the model made to produce something that matches the schema?
2. **What happens when what comes back doesn't validate?** The brief forbids silent failure,
   which rules out the tempting option of dropping it.

A useful distinction before the table: *syntactic* validity (it is JSON and has the right
keys and types) and *semantic* validity (the due date parses, the title isn't empty, the
event isn't in the year 1970). Mechanisms differ on which of the two they can guarantee.

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Raw prompt + parse + hand-rolled repair** | Works with any model on any provider. No lock-in. | The model can return prose, fences, trailing commentary, or JSON with an extra field. Every one of those is a repair case you write and maintain. Syntactic validity is a probability, not a guarantee. |
| **`instructor` (or similar)** | Wraps the provider call, validates with your Pydantic model, and on failure re-prompts with the validation error appended — automatically, `max_retries` times. The community default for exactly this problem. | A dependency that sits between you and the SDK and has to track every provider change (and 2025–2026 had several: forced `tool_choice` was removed on the newest Claude models, prefill was removed, `output_format` became `output_config.format`). The retry loop is the part worth understanding, and the library hides it. |
| **Forced tool use with a JSON Schema** | The classic pattern: define one tool whose `input_schema` is your Pydantic schema, force it with `tool_choice`. | **Rejected with a 400 on Claude Opus 5.5 / Fable 5.1**, and `strict` tool schemas exist precisely because non-strict tool input was never guaranteed valid. This is the mechanism most tutorials still show, and it is the one that has aged worst. |
| **Native structured outputs** — `output_config.format` / `client.messages.parse(output_format=Model)` | The API constrains generation to the schema derived from the *same Pydantic model* that validates the response. Syntactic validity is a guarantee, not a probability. `parsed_output` is already the typed object. No extra dependency. | Anthropic-specific; the equivalent on another provider is a different parameter. Only syntax is guaranteed: the model can still return a well-formed todo with `due: "next Thursday"` in a field typed as a date, or an empty title. Those failures are ours. |

## Decision

**Native structured outputs, with the Pydantic model as the single schema for both
constraint and validation.** The failure policy, since syntax is handled:

1. The API returns schema-valid JSON. Pydantic validators check semantics (dates parse
   relative to the capture's timestamp, titles are non-empty, confidence is in range).
2. On a semantic `ValidationError`: **one repair attempt** — the same request with the
   validation error appended as a user turn. Repair is a retry with new information, not a
   blind retry.
3. On a second failure: **terminal.** The capture moves to `failed` with
   `failure_reason = "schema_validation"` and the raw model output stored on the capture for
   inspection. It is surfaced in the app as "couldn't process this — retry?", and a
   user-triggered retry gets a fresh attempt.

A validation failure is classified as *terminal after repair* rather than *retryable*
([#17](https://github.com/akinshonibare/memo/issues/17)) because it is deterministic: the
same prompt on the same text will produce the same shape again, and retrying it with backoff
just burns money on the way to the same `failed` state.

Extraction runs with `output_config.effort = "low"`; the schema does the constraining, not
deliberation.

## What we're accepting

**We own the repair loop.** Twenty lines `instructor` would have given us, in exchange for
seeing exactly what a repair is and not depending on a wrapper tracking API churn.

**Provider lock-in at the schema boundary.** The constraint parameter is Anthropic's; a
provider swap rewrites this call, not just the model string.

**A semantic failure costs two model calls before it surfaces.** At extraction volume this
is nothing, but it is why the repair count is one and not three.

**Stored raw output on failed captures** is another column that exists only for the bad
path. It is the difference between "it failed" and knowing *why* without re-running.

## Revisit when

- Repair attempts succeed more than ~20% of the time on the golden set. That means the
  first response is often *nearly* right and a second repair is worth its cost.
- Repair attempts succeed less than ~2% of the time. Then the repair is theatre and a single
  attempt straight to `failed` is cheaper and more honest.
- A second provider is needed. `instructor` or a thin internal equivalent becomes the right
  seam at that point, not before.

## Interview angle

> **"How do you get structured output from an LLM, and what do you do when it's wrong?"**

Three mechanisms, in the order they were invented: ask nicely and parse, force a tool call
with a schema, or have the API constrain generation to the schema directly. The first is a
probability, the second was the standard until providers started removing forced tool
choice, and the third is a guarantee — for *syntax*. The part of the question that separates
answers is the second half: a schema constraint can't stop the model from putting "next
Thursday" in a date field. So I validate semantics with the same Pydantic model that
produced the schema, retry once with the validation error appended — a repair, because now
the model knows what was wrong — and then stop. Stopping is the deliberate part: a
deterministic failure retried with exponential backoff is money spent reaching the same
outcome slower. It lands in a visible failed state the user can act on, because the one
thing the product forbids is silent loss.

## Later finding (2026-09-28): the grammar follows declaration order

The first live recorded run (handoff item 4) found that one golden case (g05) failed on
**every** sample: four leaked `','confidence':1` into the idea's `summary`, and one looped on
`≥≤≥≤…` until `max_tokens`, which in production would have dead-lettered the note.

The mechanism: constrained decoding emits properties in the schema's declared order, and
`confidence` was *optional* and declared *second*. When the model skipped it and wrote
`summary`, the grammar had moved past the only slot where `confidence` was allowed. The
string was the one place left to put it. So "the constraint guarantees syntax" held; what it
also did was *push a field's content into a neighbouring string*, which syntax checks cannot
see. The leaked-field validator caught the mild cases, and nothing caught the runaway.

The fix is `json_schema_serialization_defaults_required`: in the output schema every field is
required, so there is no slot to skip (8/8 clean samples after, 0/5 before). PATCH validation
is unchanged. Extractor version bumped to `extract-v1.1`.

**Interview addendum:** "a schema constraint guarantees syntax" is true and incomplete. It
also constrains *where* the model can say things, and an optional field in the wrong place
turns intent into string content. Make fields the model should always fill required, and
put them where it will want to write them.
