# 0063 — A daily AI limit per person

**Status:** active
**Date:** 2026-10-01

## Problem

Akin: *can we add limits to users' daily AI costs?* Every note runs several model calls
(extraction, people, habits, mood, reconcile, Lately), Ask runs more, and voice runs
Deepgram. Nothing stopped one person, or one bug looping over one person's notes, from
spending without bound. The cost log (`llm_calls`) already priced every Anthropic call;
Deepgram for voice notes, live words and spoken answers wasn't in it at all.

## Options

**At the limit**

| Option | Buys | Costs |
| --- | --- | --- |
| **Pause until their midnight** | Nothing over the line is spent; nothing is lost (notes save and wait) | A note's items, people and Lately arrive tomorrow; Ask is off until then |
| Fall back to a cheaper model | Keeps working | Quality drops exactly when someone uses memo most, and the evals only cover Opus |
| Warn only | No friction | Not a limit |

**Counting**

| Option | Buys | Costs |
| --- | --- | --- |
| **Sum the cost log since their local midnight, checked before each paid call** | One source of truth, already written by every call; exact; indexed (`user_id, created_at`) | A query per paid stage; a call already under way can take the day a little past the line |
| A Redis counter per user per day | No database read | A second record of spend to keep in step with the first, and lost on a Redis flush |
| Reserve an estimate before each call | Never over | Estimates for calls whose size isn't known until they finish |

## Decision

**Each person gets a daily AI budget, $1 by default (`DAILY_AI_LIMIT_USD`; 0 turns it
off; *lowered to $0.50 on 2026-10-01 at Akin's ask, with five users and a typical day of
one person's notes at about $0.40 on the 0065 routing; the Flash-Lite moves in
[0067](0067-gemini-behind-the-same-seam.md) take a day well under it*), overridable per person by the operator (`users.daily_ai_limit_usd`, migration
0029; NULL means the default, 0 means none; no route writes it).** Spend is the sum of
their `llm_calls` since their own midnight. Before anything paid starts:

- **A note** always saves. Each paid pipeline stage (`Stage.paid`: transcribe, describe,
  extract, people, faith, mood, habits, reconcile) checks first; over the limit, the
  stage's claim is released (not an attempt, not a failure) and the job is deferred to
  their midnight plus up to five minutes, so waiting notes don't all start at once.
  Scripture (code) and embedding (a fraction of a cent) don't wait.
- **Backlogs and Lately** skip someone at their limit and pick them up after midnight.
- **Ask, spoken questions and spoken answers** return 429, like the rate limits, with
  "You've reached today's AI limit. Your notes still save, and Ask is back after
  midnight." and a `Retry-After`. **Live words** are refused like a rate limit; the
  recording itself carries on.
- **Now in the cost log:** voice-note transcription ($0.0043/min), live words
  ($0.0048/min) and spoken answers ($0.030 per 1,000 characters), Deepgram's
  pay-as-you-go prices read off its pricing page on 2026-10-01.

The app shows "Today: $0.42 of $1.00" in Settings → Usage, and a line on Home when the
limit is reached. `python -m memo.admin.costs` (read-only, in the api container) lists
every user's spend today and over a window, and by stage.

## What we're accepting

- **Slightly over, never far over.** The check is before a call, so the call that
  crosses the line finishes. With calls of about a cent, a day ends within a few cents
  of its limit.
- **Voyage embeddings aren't counted** (thousandths of a cent a note). If embedding ever
  costs real money, price it like Deepgram.
- **$1 is a guess** from per-call costs (a note read by every reader ~$0.05, an Ask
  ~$0.04), not from a month of real use. A heavy day reaches it by evening.
- **The limit is the operator's to change**, by SQL in the api container. Fine for a
  handful of users; an admin command once there are more.

## Revisit when

- People hit the limit on ordinary days: raise the default, or make cheap stages
  (people, mood) cheaper before raising anything.
- Spend is ever material across users: a global daily cap as a kill switch, on top of
  the per-person one.

## Interview angle

> **"How would you stop an LLM feature from running up an unbounded bill?"**

Log the price of every call where it's made, then budget against that log: per user,
per day in their timezone, checked before each paid call. Decide what "over" means per
surface: background work defers (nothing lost), interactive features refuse with a clear
message and a retry time, capture never refuses. Then add the levers: per-user
overrides, and an operator report. A rate limit bounds requests; a budget bounds money,
which is what you actually care about.
