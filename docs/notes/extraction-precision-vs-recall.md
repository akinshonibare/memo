# Precision vs recall for extraction, and why the gate is asymmetric

> **DRAFT written by Claude for issues #24/#25.** Rewrite in your own words on the
> learning pass.

## In one sentence

For memo's extractor a missed todo and an invented todo are not equally bad, so the
merge gate demands higher recall than precision and the eval measures each per kind.

## What problem does it solve / what did people do before it

"It looked good in testing" is not an eval. An eval is a labelled set, a scoring rule,
and a threshold that fails the build. Without one, prompt changes are vibes: you
re-read five outputs, they look fine, you ship, and three weeks later you notice todos
have been quietly going missing since a "small wording tweak."

Precision is *of the things it produced, how many were right*; recall is *of the things
that were there, how many did it find*. They trade against each other through the
extractor's threshold for "is this really a todo?" — a cautious extractor has high
precision and low recall, an eager one the reverse. Which matters more depends on the
cost of each mistake **to the user**, not to the metric:

- A **missed** todo (recall failure) is silent. The user doesn't know it's missing; the
  thing doesn't get done; trust erodes without a visible moment to blame.
- An **invented** todo (precision failure) is loud. It appears on the review screen, is
  obviously wrong, and is one tap to dismiss — and the dismissal is a labelled example.

So memo weights recall higher for todos: recall ≥ 0.90, precision ≥ 0.80 (NFR4). Ideas
get a looser bar in both directions because "is this idea-worthy" is genuinely fuzzier.

Two design details carry this into practice. The `PATCH` and `POST` endpoints on
derivations are the intake pipe for new labelled examples — a `PATCH` is a precision
signal (it was there but wrong), a `POST` is a recall signal (it was missed). And
`extractor_version` on every row makes "re-run the new extractor over last month and
diff" a query, so a threshold regression is diagnosable rather than just red.

## The thing I got wrong first

Building the extractor first and the eval set second. The set has to exist *before* the
extractor is any good, so the number can be watched moving — otherwise the first score
you see is already the one you're anchored to, and you never learn what the easy cases
were.

## How I'd explain it to someone in an interview (30 seconds, out loud)

An eval is a labelled set, a scoring rule, and a threshold in CI; anything less is a
demo. For extraction I score precision and recall per kind, and I don't weight them
equally: a missed todo fails silently and costs trust, an invented one is visible and
one tap to dismiss, so recall gets the higher bar. The correction endpoints are the
labelling loop — every fix a user makes is a new example, and whether they edited or
added tells me which metric it feeds. The thing I'd add unprompted: the prompt is
versioned and stamped on every row, so when the gate goes red I can diff two versions
over the same history instead of guessing.
