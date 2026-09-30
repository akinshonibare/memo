# 0061 — A tone for how memo writes, and no em dashes for anyone

**Status:** active
**Date:** 2026-09-30

## Problem

Akin: *can we add a setting for the tone of the summaries and text generated, so the
user can set how the vibes are; also reduce the use of em dashes, that's a global
setting.* memo writes prose for people in three places: Ask's answers (written and
spoken), highlight summaries, and photo descriptions. All of it sounded the same, and
all of it leaned on the em dash, the tell of machine-written text.

## Options

**Tone: where it lives**

| Option | Buys | Costs |
| --- | --- | --- |
| **Four presets on the account (plain, warm, brief, playful), a line appended to the prompts that write prose** | Four stable prompts per template, so the prompt cache still hits; each preset can carry its own guard ("playful never jokes about grief") | A fixed menu; someone who wants "dry British" can't say so |
| Free text ("describe the vibe you want") in the prompt | Any voice at all | Untestable, and it's user text inside every prompt: an injection surface and a new way for answers to go wrong that no eval covers |
| Tone per feature (Ask one way, summaries another) | Precision | Four settings nobody asked for |

**Tone: what it touches.** Titles and labels stay plain (you scan them); quotes from a
note are the author's words and never change. Chosen: Ask's answers, highlight
summaries, photo descriptions (Akin picked this over "Ask only" and "everything").

**No em dashes**

| Option | Buys | Costs |
| --- | --- | --- |
| Tell the model | Sentences are written without them, properly | Mostly; a model told not to still sometimes does |
| Strip them after | Guaranteed | A comma where a dash was is sometimes clumsy, and streamed text has to be held back a token to do it well |
| **Both** | The model restructures; the filter catches what's left | Two places to change if the rule ever changes |

## Decision

**`users.tone` (migration 0027): plain by default, or warm, brief or playful, set in
Settings → Assistant. `prompts/voice.py` holds the four tone lines and the no-em-dash
rule; the answer prompt appends them (`answer-v2.5`), the extraction prompt asks for
summaries in the tone and never an em dash (`extract-v1.2`), and the describe prompt
does the same for descriptions (`describe-v1.2`).** An unknown stored tone is plain.

**`services/prose.plain_dashes` makes every em dash in memo's own writing a comma**:
extracted titles, summaries and notes; photo descriptions (not the text read off the
photo); what the people reader writes about someone; prayer titles. Ask's answer goes
through `DashFilter` as it streams, which holds back trailing spaces and dashes until
the next token shows what follows, so the text you watch appear is the text saved.
**Anything the person wrote keeps its dashes**: iOS turns "--" into an em dash as you
type, and that one is theirs. En dashes stay ("John 3–4").

The extraction eval was re-recorded at plain for v1.2 and re-judged: todo 1.00/0.96,
event 1.00/1.00, idea 0.89/1.00, the same two known misses as v1.1, and no em dash in
any output.

## What we're accepting

- **The tone isn't stamped on the rows it shaped.** Prompt versions are; the tone is a
  parameter of them. A summary written warm last month and one written plain today
  carry the same `extract-v1.2`. If tone ever needs auditing, stamp it on `llm_calls`.
- **Only plain is evaluated.** The evals run at the default; warm, brief and playful
  are trusted to change wording, not facts, because the prompt says so and the eval's
  structure doesn't depend on wording. A playful answer that gets a date wrong would
  not be caught before someone sees it.
- **Changing tone doesn't rewrite anything.** It applies from the next answer and the
  next note on.
- **A comma isn't always the best replacement** for a dash the model insisted on. The
  prompt makes that rare; the filter makes it never a dash.

## Revisit when

- People ask for a tone that isn't one of the four more than once: add a preset (with
  its own guard), still not free text.
- A tone-specific failure shows up (a brief answer dropping a caveat, a playful one on
  a hard note): add that tone to the answer eval.

## Interview angle

> **"How would you let users customise an LLM's voice without breaking quality?"**

Offer a small set of named presets instead of free text: each is a fixed prompt
variant you can test, cache and give its own guard rails, and none is an injection
surface. Keep the customisation to style ("changes how things are said, never what is
true"), keep structured fields and quotes out of its reach, and run your quality evals
at the default so the gate still means something.

> **"The model keeps doing X even though the prompt says not to."**

Ask in the prompt, then enforce in code at the boundary where the text leaves the
model: a deterministic post-processor on every field the model writes, and a streaming
version that holds back just enough to decide. Prompts express intent; code guarantees
invariants.
