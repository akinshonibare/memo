# 0065 — Sonnet 5 for four readers; Opus 5 stays where a miss hurts

**Status:** active
**Date:** 2026-10-01
**Revisits:** [0014](0014-claude-opus-5-for-extraction.md) (extraction stays on Opus; 0014
stands)

## Problem

Akin: *how can we save costs?* Every reader ran on `extraction_model`, Opus 5, because 0014
picked the strongest model for extraction and the later readers (people, habits, faith,
mood, reconcile, Lately) inherited the setting without their own decision. On
2026-09-30 the readers were about 73% of all model spend (extract $0.14, habits $0.13,
people $0.12, mood $0.07, Lately $0.06, reconcile $0.04, faith $0.03 of $0.62). 0014 named
its own trigger: drop down when the eval shows a cheaper model within 2 points of Opus.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Stay on Opus 5 everywhere ($5/$25 per Mtok) | Nothing to re-verify | Paying 2.5× per call on readers that score the same on Sonnet |
| **Sonnet 5 per reader, where its eval holds** ($2/$10) | 60% off each moved reader's calls, measured; slightly faster | A setting per reader; each moved reader's recording is Sonnet's; two models to watch |
| Haiku 4.5 per reader ($1/$5) | 80% off | Failed habits `date_accuracy` (0.89 < 0.90) on the first pass; rejects `output_config.effort`, so the provider needs a branch; not pursued |
| Sonnet everywhere, including extraction | ~50% off in total | Fails gates: see below |

## What we measured

Live runs of each eval on both models (2026-09-30, about 23:00 EDT). Sonnet ran each
reader three times, plus the recording; Opus ran each once, and three times where Sonnet
missed. Cost per call is from real `llm.call` token counts, not price lists.

| Reader | Sonnet 5 | Opus 5 | Sonnet $/call vs Opus | p50 latency |
| --- | --- | --- | --- | --- |
| people | 1.00 on every metric, 4/4 runs | 1.00 | $0.0047 vs $0.0118 | 1.8s vs 2.1s |
| habits | 1.00, 4/4 | 1.00 | $0.0056 vs $0.0140 | 1.8s vs 2.0s |
| faith | 1.00, 4/4 | 1.00 | $0.0032 vs $0.0080 | 1.6s vs 1.8s |
| reconcile | passes 4/4; precision 0.94 once (bar 0.90) | 1.00 | $0.0033 vs $0.0084 | 1.4s vs 3.4s |
| **mood** | **`no_sign_flip` 0.96 in 1 of 3** (bar 1.00) | 1.00, 3/3 | $0.0038 vs $0.0096 | 1.4s vs 1.9s |
| **Lately** | **`no_guessed_pronouns` 0.90 in 1 of 4** (bar 1.00) | 1.00, 3/3 | $0.0022 vs $0.0057 | 1.6s vs 1.9s |
| **extraction** | **todo recall 0.92, then 0.88** (bar 0.90) | 0.96 | $0.0055 vs (Opus) | 1.9s |

The misses that decided it:

- **mood, m30**, "Thank God the flight landed, the turbulence was mad": Sonnet read −1
  (shaken) instead of relief. A flipped feeling is the worst thing the Within screen can
  show.
- **Lately, l01**: Sonnet wrote "with her" about Kemi; nothing in the notes gives Kemi's
  pronouns. That is the misgendering the gate exists for. It also wrote a filler line for
  l04, where the right answer was nothing.
- **extraction**: missed g04 ("cancel the thursday call with the accountant") both runs and
  g39 once. 0.92 and 0.88 against Opus's 0.96 is outside 0014's 2-point rule.

## Decision

**people, habits, faith and reconcile run on Sonnet 5; extraction, mood, Lately and Ask
stay on Opus 5.** Each reader has its own setting (`PEOPLE_MODEL`, `HABITS_MODEL`,
`FAITH_MODEL`, `RECONCILE_MODEL`, `MOOD_MODEL`, `LATELY_MODEL`; extraction keeps
`EXTRACTION_MODEL`, which photo descriptions also use), so the choice is per reader and
measured per reader.

Expected saving: those four were $0.32 of the $0.62 day; at 0.40× that is about $0.19 a
day, **roughly 30% of total spend**.

## How to go back

For one reader, no code change needed:

1. In Railway, on the **worker** service (and the api, so both agree), set e.g.
   `PEOPLE_MODEL=claude-opus-5` and apply the staged change. Railway redeploys the same
   image with the new variable.
2. New notes are read by Opus from then on. Old rows keep the model they were read with in
   `extractor_version` (`people-v1/claude-sonnet-5`), so it's visible which model wrote
   what. Nothing re-reads history: every backlog sweep picks notes a reader has *never*
   read, at any version.
3. In code, change the default in `config.py` and re-record that reader's eval on Opus
   (`railway run -s worker -- uv run python -m evals.people.run --model claude-opus-5
   --record evals/people/recorded.json`), or CI keeps replaying Sonnet's answers.

To move another reader *to* Sonnet later, run its eval on `--model claude-sonnet-5` at
least three times and move it only if every run clears every bar.

## What we're accepting

- **The evals are easy, and Sonnet's margin on them is unknown.** Four 1.00 runs on 22–44
  starter cases say Sonnet isn't worse *on these cases*. Real notes are messier; a miss
  Opus wouldn't make could reach a user before an eval case covers it.
- **Variance is real and we sampled it thinly.** Sonnet failed a hard gate in 1 of 3–4
  runs on two readers. The moved four never did, but four runs can't rule out a 1-in-10
  miss.
- **CI now replays Sonnet's answers** for the moved readers. A prompt change is checked
  against Sonnet, and Opus's numbers for those readers are only in this file.
- **More settings to keep straight.** Seven model knobs instead of one. The reason it's
  worth it: the reader that most needs the strong model (mood) and the one that least does
  (faith) were sharing a setting for no reason.

## Revisit when

- A user-visible miss on a moved reader that Opus gets right: set that reader back first,
  then add the case to its golden set.
- The golden sets grow (a reader's set doubles, or gains real-note cases): re-run both
  models; the comparison above was on starter sets.
- Spend passes ~$5/day, or a new model changes the price/quality curve: re-run every
  reader on every candidate, Haiku included. *Done for Gemini on 2026-10-01 in
  [0067](0067-gemini-behind-the-same-seam.md).*

## Interview angle

> **"How did you cut your LLM costs without hurting quality?"**

Per-stage model routing, decided by evals rather than by instinct. Each pipeline stage
has its own golden set and merge-gate thresholds, so the question "can a cheaper model do
this" has a measured answer per stage. I ran each stage several times on both models,
because one passing run tells you nothing about variance, and priced the calls from real
token counts. Four stages held on Sonnet at 40% of the cost. Three didn't: mood flipped a
feeling's sign, the summary writer guessed someone's pronoun, and extraction lost four
points of recall. So those stayed on Opus. The senior part is the hard gates (some errors
are worth paying 2.5× to avoid) and the rollback: it's an environment variable, and every
row records which model produced it.
