# 0067 — A model setting names its provider: Gemini behind the same seam, measured per reader

**Status:** active
**Date:** 2026-10-01
**Builds on:** [0014](0014-claude-opus-5-for-extraction.md) (the model at the exposed
call site), [0015](0015-structured-outputs-and-terminal-validation.md) (one Pydantic model
constrains and validates), [0065](0065-sonnet-for-four-readers.md) (a setting per reader,
decided by its eval), [0066](0066-wait-out-an-unavailable-provider.md) (whose fault is a
failure)
**Revisits:** 0015 named its own trigger: *"A second provider is needed. `instructor` or a
thin internal equivalent becomes the right seam at that point, not before."* That point
is now.

## Problem

Akin: *reduce the Anthropic bill, support other providers, tell me whether Gemini is good
enough to replace, and what the free tier gets us.* After 0065 memo spends about $0.45 a
day on models, roughly $13 a month, all of it with one company. 0066's incident (an empty
balance stopped every stage at once) showed what that single dependency costs when it
blinks.

Two questions, kept apart because their answers differ:

1. **How does memo call a second provider** without a second copy of every stage?
2. **Is a given Gemini model good enough for a given reader**, at what price, and on what
   terms?

## Options for the seam

| Option | What it buys | What it costs |
| --- | --- | --- |
| Stay single-vendor; keep tuning models (0065's path) | Nothing to build | The price floor is Haiku's; one company's outage or billing stops everything |
| Google's `google-genai` SDK | Typed client, built-in retries, Google's newest API first | **Pins `websockets<17`; memo's live transcription needs 17.** Ten transitive packages (google-auth, requests, tenacity). Its retries would fight the pipeline's own (0066). Pushes the Interactions API, which stores conversation state server-side by default |
| A gateway or abstraction library (LiteLLM, `instructor`, OpenRouter, Vercel AI Gateway) | One interface over a hundred providers; the answer an interviewer expects | A dependency between us and every provider, tracking every API change (0015 declined `instructor` for this). It hides exactly the two things memo cares about: how structured output is constrained, and which error means what. A hosted gateway is a third company that reads every note, for a markup |
| **A thin seam of our own over the REST API (httpx)** | About 300 lines in the shape the repo already has (Voyage, Deepgram): the same transformed schema, our error taxonomy, no new dependency. A model name picks the provider | We maintain two request and response shapes, and read Google's changelog ourselves. Each further provider is a module, not a config line |

## Decision for the seam

**A model setting names its provider by prefix, and one factory builds the client.**
`claude-*` goes to Anthropic, `gemini-*` (or `gemma-*`) to Google; anything else fails at
startup with the list of known prefixes. Concretely:

- `services/providers.py` is the one place that turns a model name and a schema into a
  provider. The seven readers, photo descriptions, Ask's answer and its rewrite all call
  it, so routing a stage to Google is the same change 0065 made it to route to Sonnet:
  one setting, e.g. `PEOPLE_MODEL=gemini-3.5-flash-lite`, and a `GEMINI_API_KEY`.
- `services/gemini.py` is the provider: `generateContent` and `streamGenerateContent`
  over httpx, with the stages' Anthropic-shaped messages translated to `contents`
  (text and base64 images), the system prompt to `systemInstruction`, and the schema to
  `responseJsonSchema`. It is the **same transformed schema** Anthropic constrains with,
  every field required, so 0015 holds unchanged: one Pydantic model both constrains and
  validates, and the repair loop in `complete_validated` never knew which company
  answered. All eight of memo's schemas were accepted by Gemini's constrained decoding on
  the first try (`$defs`, `$ref`, `anyOf` nullables, enums, `format: date`).
- `RawCompletion.provider` says who answered, so `llm_calls.provider` and the cost page
  are right per row without a stage knowing.
- Failures are classified by whose fault they are, as 0066 asks. Per-minute 429 and 503
  ("high demand", common on the free tier) are blips. A **per-day quota 429** lasts until
  Google's midnight, so it is `ProviderUnavailable`: waited out without spending a
  note's attempts. So are 401/402/403, `FAILED_PRECONDITION` (billing), and a 404 for a
  model Google no longer serves, which it does to whole generations (`gemini-2.5-flash`
  returns 404 "no longer available to new users").
- Photo descriptions became provider-neutral on the way: `PhotoDescriber` builds one
  image-plus-text message and hands it to a structured provider, instead of carrying its
  own copy of the Anthropic call.
- The privacy page derives "who processes it" from the model settings. It cannot name a
  company the configuration doesn't use, or leave one out; a test checks both.
- Pricing: Gemini models are in the price table at paid-tier list price. Thinking tokens
  bill as output and are counted so; cached input bills at 0.1x, the same multiplier as
  Claude's.

## Google's terms: the free tier is for the evals, never for notes

The free tier is the whole reason the question was asked, so the finding comes first.
Google's Gemini API terms, read 2026-10-01, for **Unpaid Services**: Google *"uses the
content you submit ... to provide, improve, and develop Google products"*, *"human
reviewers may read, annotate, and process your API input and output"*, and *"Do not
submit sensitive, confidential, or personal information to the Unpaid Services."* A
private journal is all three. For **Paid Services** (a project with a linked billing
account), Google *"doesn't use your prompts ... or responses to improve our products"*
and logs them only for abuse detection.

So:

- `GEMINI_PAID_TIER` (default false) says which terms apply. The privacy page states
  Google's free-tier terms in plain words until it is true, and the no-training claim
  only once it is.
- Outside `local` and `test`, a stage set to a Gemini model **refuses to start** without
  `GEMINI_PAID_TIER=true`, with a message that says why. This is the one place memo
  enforces a vendor's terms in code rather than by remembering them; a journal app
  sending notes to a service whose terms exclude personal information is not a
  configuration mistake we want to be possible.
- The evals' golden sets are synthetic notes Claude wrote. Running them on the free tier
  is the tier's legitimate use here, and it is how everything below was measured.

**Free credits.** There are none that apply. Google Cloud's $300 welcome credit is
explicitly excluded from Gemini API and AI Studio usage for accounts opened after
2026-03-02 (Google's billing page). Anthropic offers none for an existing account. The
paid tier needs a $5 minimum prepayment. So "free" means the free tier, and the free
tier means Google's training terms plus quotas Google no longer publishes (AI Studio
shows the live ones per project). What this project saw on 2026-10-01:

- **`gemini-3.8-flash`**: unusable. Two probes, four minutes apart, were shed with
  `503 UNAVAILABLE "high demand"` on six and seven consecutive tries; the handful of
  calls that got through then hit `429` with
  `quotaId: GenerateRequestsPerDayPerProjectPerModel-FreeTier` before one 22-case eval
  pass finished. The classifier read that id and raised `ProviderUnavailable`, so in
  production a note would have waited rather than failed. 3.8 Flash can only be
  measured, or run, on the paid tier.
- **`gemini-3.5-flash-lite`**: about 450 calls at 15 a minute, then the same per-day
  quota id, midway through extraction's second run. That is the ~500 requests a day
  third parties report. Shedding (503, retried in seconds) set in after an hour and
  pushed p50 latency from under a second to 3 to 8 seconds; free-tier latency is not a
  measure of the model. Enough for three passes of four readers and one or two of the
  rest, which is what the table below is.

## What we measured

Live runs of each eval on `gemini-3.5-flash-lite` (thinking level low), 2026-10-01, on
Google's free tier, against the same golden sets and bars as 0065. Sonnet and Opus
columns are 0065's measurements. Cost per call is from real token counts at paid-tier
list price; extraction is scored with the Claude judge (`--match`), as 0065's was.

| Reader | Flash-Lite | Runs | Sonnet 5 (0065) | Opus 5 (0065) | $/call: Flash-Lite, Sonnet, Opus | p50 (unshed) |
| --- | --- | --- | --- | --- | --- | --- |
| people | 1.00 on every metric | 3/3 | 1.00, 4/4 | 1.00 | $0.0004, $0.0047, $0.0118 | 0.9s |
| habits | every metric above its bar; one amount missed once (h35) | 3/3 | 1.00, 4/4 | 1.00 | $0.0005, $0.0056, $0.0140 | 0.8s |
| faith | 1.00 on every metric | 3/3 | 1.00, 4/4 | 1.00 | $0.0003, $0.0032, $0.0080 | 0.8s |
| **reconcile** | **precision 0.89 (bar 0.90), the same two false links both runs** | **0/2** | passes 4/4 (0.94 once) | 1.00 | $0.0003, $0.0033, $0.0084 | 0.9s |
| mood | 1.00 on every metric, `no_sign_flip` included | 2/2 | `no_sign_flip` 0.96 in 1 of 3 | 1.00, 3/3 | $0.0004, $0.0038, $0.0096 | 3.4s (shed) |
| Lately | `key_facts` 0.90 (bar 0.85), the five 1.00 gates all 1.00 | 1/1 | `no_guessed_pronouns` 0.90 in 1 of 4 | 1.00, 3/3 | $0.0002, $0.0022, $0.0057 | 5.6s (shed) |
| extraction | todo 1.00 / **0.92**, event 1.00 / 1.00, idea 0.80 / 1.00 | 1/1 | todo recall 0.92, then 0.88 | **0.96** | $0.0004, $0.0055, ~$0.014 | 4.2s (shed) |

The misses that decide it:

- **reconcile, r08**: "thank God the flight landed safely, that turbulence", with the
  prayer "the Lagos job" listed. Flash-Lite marked the prayer *answered* (confidence
  0.9). **r20**: a new idea, "memo could also notice when prayers get answered", listed
  next to "memo could suggest habits from notes". Flash-Lite marked it as *continuing*
  that idea. Both are "related topic" links where the right answer is nothing, both
  happened in both runs, and a wrong "answered" rewrites a prayer's story (0048).
  Sonnet gets both right. This is a model that links too eagerly, not noise.
- **extraction**: todo recall 0.92 against Opus's 0.96, from the same two cases Sonnet
  and Opus miss: g04 ("cancel the thursday call with the accountant") and g36 ("compare
  quotes" folded into the renewal todo). Four points is outside 0014's two-point rule,
  the same margin that kept Sonnet off extraction. Flash-Lite equals Sonnet's better
  run here at a fourteenth of its price, but equal to Sonnet is not the bar.
- **habits, h35** once: an amount missed in one run of three; `amount_recall` stayed above
  its bar. **Lately, l01**: a key fact dropped in its one run; the gate that failed Sonnet
  on that case (a guessed pronoun) held.

## Decision for routing

**people, habits and faith can move to `gemini-3.5-flash-lite`; reconcile cannot;
extraction stays on Opus; mood and Lately need their remaining runs first.**

- The three movers cleared every metric on nine of nine runs, as Sonnet did on its
  twelve. At $0.0004 a call against Sonnet's $0.0047 they cost about 8% of what they do
  now. On 0065's reference day those three were $0.11 after the Sonnet move; this takes
  them to about $0.01, roughly **a quarter of the whole model bill**.
- **Reconcile stays on Sonnet.** A deterministic pair of false "answered"/"continues"
  links is exactly what its precision gate exists to stop.
- **Extraction stays on Opus**, by 0014's own rule, and 0014 stands for a third time.
- **Mood (2 of 2) and Lately (1 of 1)** both held gates that failed Sonnet, which is
  promising, but 0065 set the bar at three clean runs before moving a 1.00-gate reader.
  One more mood run and two more Lately runs decide it, on the paid tier (about two
  cents) or on the free tier after its reset. If they hold, that is another ~28% of the
  bill.
- **Nothing in this change moves a default.** The movers' defaults stay on Sonnet until
  billing is linked and `GEMINI_PAID_TIER=true` is set, because a Gemini default
  deployed before that would stop the worker at startup (the guard), and a free-tier
  deployment is the thing this ADR forbids. The switch is the procedure below, then a
  follow-up commit that moves the three defaults and re-records their evals.


## How to switch a stage, and how to go back

Switching is 0065's procedure with one more variable:

1. In AI Studio, link a billing account to the project (Google's paid tier) and
   confirm it there. On the free tier the worker will refuse to start the stage.
2. In Railway, on **both** services, set `GEMINI_API_KEY` (or run
   `scripts/push_secrets.sh` with it in the local `.env`), `GEMINI_PAID_TIER=true`, and
   the stage, e.g. `PEOPLE_MODEL=gemini-3.5-flash-lite`. Apply the staged change; Railway
   redeploys the same image.
3. Replace that reader's CI recording with a run on the new model, read the misses, and
   commit it: `uv run python -m evals.people.run --model gemini-3.5-flash-lite --record
   evals/people/recorded.json`. The default in `config.py` changes in the same commit, so
   `tests/test_reader_models.py` keeps the defaults and the recordings in step.
4. Check the privacy page: it now names Google and the paid-tier sentence.

Going back is the first half in reverse: set the stage's variable to its Claude model
(new notes use it at once; old rows keep `people-v1/gemini-3.5-flash-lite` in
`extractor_version`, so it stays visible which model read what), then re-record the
eval on Claude. Removing `GEMINI_API_KEY` takes Google off the privacy page only once no
stage is set to a Gemini model; the page follows the model settings, not the key.

## What we're accepting

- **Two request shapes to keep right.** Google's `generateContent` is "legacy, fully
  supported" since the Interactions API went GA in June 2026. No shutdown date is
  announced. When one is, this module is the whole surface to change, and the stages
  don't move.
- **The recorded model is the one we asked for**, not Google's `modelVersion` (which can
  carry a build suffix). The price table is keyed by what we ask for; Anthropic's path
  records the response's name, which has always matched.
- **The daily AI limit counts list price even on the free tier.** A free call shows as
  costing its paid price, so the cost page answers "what would this cost if we paid", which
  is the question that decides whether to pay. The invoice is the truth about money; the
  column is the truth about usage.
- **No eval guards Ask.** `answer_model` and `rewrite_model` can be routed, and were
  smoke-tested live on Gemini (a 521 ms first token, a sensible rewrite), but no golden
  set scores an answer's quality. Ask stays on Claude whatever the price until one
  exists.
- **The seam is two `if`s, not a plugin system.** A third provider adds a module and a
  branch in `providers.py`. That is the right size for two; at four it should become a
  table on `ProviderSpec`.
- **Free-tier quotas are per model per day**, so a note that trips one waits like an
  outage, up to the 0066 ceiling. On the paid tier the limits are hundreds of times
  higher and this does not arise.
- **`gemini-3.8-flash` is unmeasured.** The free tier shed every probe and its day's
  quota is a handful of calls. It costs 2.5x Flash-Lite and sits below Sonnet; whether it
  closes extraction's four points is a paid-tier question (about three cents a run).
- **Still starter sets.** Every caveat in 0065 about 22 to 44 synthetic cases applies.
  Real notes are messier, and a Flash-Lite miss that Sonnet wouldn't make can reach the
  app before a case covers it. The rollback is a setting, and every row names its model.

## Other ways to cut the bill, considered

| Idea | Saves | Why not now |
| --- | --- | --- |
| Prompt caching on Anthropic | 90% on the cached system prompt | Every reader's system prompt is 370 to 910 tokens, under the 1,024-token minimum Claude caches. Nothing to cache until prompts grow |
| Batch APIs (Anthropic Message Batches, Gemini Batch) at 50% off | Half the cost of the background readers (people, habits, faith, mood, Lately, reconcile are not latency-bound) | A second submission path and a results poller; worth it when spend is dollars a day, not cents. The best quality-neutral lever after this one |
| One call for all readers | Six fewer calls a note | Six evals become one, every reader's miss becomes everyone's re-record, and 0065's per-reader routing disappears. Rejected |
| Keyword gates before a reader (skip faith on a note with no faith words) | A share of calls on notes with nothing for that reader | Recall loss by construction, invisible until a case covers it. Reconcile already has a gate because its evidence is structural (an earlier item exists), not lexical |
| Haiku 4.5 per reader | 80% off | Failed habits `date_accuracy` in 0065 and needs an `effort` branch in the Anthropic provider. Cheaper Gemini models now sit below Haiku's price with evals to show for it |

## Revisit when

- Google announces a `generateContent` shutdown date, or changes the free tier's terms
  (then the guard's wording is wrong, not just strict).
- A reader's golden set gains real notes: re-run every candidate, as 0065 asks.
- A user-visible miss on a Gemini-routed reader that Claude gets right: set it back
  first, add the case, then re-run.
- The 3.6 to 3.8 Flash promotional prices end (2026-12-31): re-check the table.
- A third provider is wanted: lift the key lookup onto `ProviderSpec` before adding it.

## Interview angle

> **"How would you add a second LLM provider to a system built on one, and how do you
> decide what to move?"**

Find the seam you already have. Every stage here already called one Protocol,
`complete(system, messages) -> RawCompletion`, because structured output and the repair
loop had been separated from the SDK call a month earlier. So the second provider was a
module that speaks that Protocol over HTTP, and a factory that reads the provider off the
model name. No stage changed. I didn't take the SDK (it conflicted with a pinned
dependency and brought its own retries into a pipeline that owns retrying) or a gateway
library (it hides the two things I need to see: how output is constrained, and which
error means what). Then the deciding part is per stage and measured: each reader has a
golden set and hard gates, so "is the cheap model good enough" is a number per reader,
run several times because one pass says nothing about variance. And before any of it,
read the terms: the free tier that made the question interesting trains on what you send
and excludes personal data, so the code refuses to run it against real users, and the
evals' synthetic notes are the only thing it ever sees.
