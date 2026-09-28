# Query rewriting for multi-turn, and what it costs in milliseconds

> **DRAFT written by Claude for issue #35.** Rewrite in your own words on the learning
> pass.

## In one sentence

"What about the other one?" retrieves nothing on its own, so a follow-up is rewritten
against recent history into a standalone query before retrieval — an extra model call
that sits directly on the time-to-first-token budget, which is why it is skipped
whenever it can be.

## What problem does it solve / what did people do before it

Retrieval embeds the *question*. A first question — "what did I need to talk to Tunde
about?" — carries its own signal. A follow-up — "and what about him?" — carries none:
"him" is resolved by the conversation, not by the journal, and embedding it finds
nothing useful. Single-shot RAG simply doesn't have this problem; conversational RAG
does, and the answer is to rewrite the follow-up into the question it *means* ("Tunde
pricing call") using the last few turns, then retrieve on that.

The cost is a model round-trip *before* retrieval can even start, on a path whose
budget is under a second to first token (NFR3). memo does the two cheapest mitigations
from the design doc: skip on turn one (no history to resolve against), and skip when the
question has no pronoun or deictic marker (a regex — "it", "that", "him", "the other one"
— routes most questions straight past the rewrite). The rewrite uses a small model
(Haiku), records its own `llm_calls` row, and stores `rewrite_ms` and `ttft_ms` on the
message so the cost is a column, not a guess. The design doc's next step — speculative
retrieval on the raw query in parallel with the rewrite — is not implemented until the
stored numbers say TTFT is over budget.

Context management is the other multi-turn tax: the last six turns go into the prompt
verbatim; anything older is dropped rather than summarised, because a summarised turn
has lost its citations and pretending otherwise produces confidently mis-attributed
answers.

## The thing I got wrong first

Rewriting every turn. It is the obvious implementation and it puts 200–400ms in front of
every answer for a benefit only follow-ups get. The rule that matters is: *only pay for
context resolution when the question needs context.*

## How I'd explain it to someone in an interview (30 seconds, out loud)

Multi-turn RAG breaks retrieval because follow-ups reference the conversation, not the
corpus — "what about him" embeds to nothing. So I rewrite follow-ups into standalone
queries against recent history before retrieving. That's an extra model call on the
critical path to first token, so I skip it on the first turn and whenever the question
has no pronouns or references, use a small fast model when I do call it, and record the
milliseconds on the message row so the cost is measured rather than assumed. If the
numbers say it's over budget, the next move is speculative retrieval on the raw query
in parallel with the rewrite — hide the latency behind work you needed anyway.
