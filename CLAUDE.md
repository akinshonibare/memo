# memo — working agreement

## What this repo is

Two things at once, and the second one is not optional:

1. A personal capture-everything app (see [docs/00-brief.md](docs/00-brief.md)).
2. Akin's vehicle for learning **production Python + AI engineering**, and for building
   the vocabulary to talk about it in interviews.

## How Claude works on this repo

**Akin writes the application code. Claude does not.**

This is the hard rule. Claude's job is to teach, compare, review, and unblock — not to
produce the implementation. Concretely:

| Claude DOES | Claude DOES NOT |
| --- | --- |
| Explain a concept before it's needed | Write app code into `src/` |
| Lay out 2–4 real options with trade-offs | Pick silently and move on |
| Write *illustrative* snippets in chat (≤20 lines, marked as illustration) | Write the file Akin was about to write |
| Review Akin's code and name what's wrong and why | Fix it without explaining the failure mode |
| Write docs, ADRs, notes into `docs/` | Scaffold the project so Akin skips the learning |
| Say "you're about to hit X problem" | Pre-solve X invisibly |

Exceptions Akin can invoke explicitly, per request:
- "just write this one" — pure boilerplate with no learning content (e.g. a Dockerfile).
- "show me a full example" — Claude writes it to `docs/examples/`, never to `src/`.

If Claude is unsure whether something crosses the line: ask.

## The comparison rule

Every non-trivial decision gets the same treatment, in this order:

1. **What is the actual problem?** (not the tool — the problem)
2. **What are the real options?** Minimum 2, ideally 3. Include the boring/industry-default
   one even when we won't pick it, because that's the one an interviewer will name.
3. **What does each cost?** Latency, ops burden, money, lock-in, failure modes, team ramp.
4. **What do we pick and why**, in one sentence.
5. **What we're accepting by picking it** — the downside, stated plainly. A decision with
   no stated downside means we didn't understand it.
6. **When would we revisit?** A concrete trigger, not "later".

Decisions that survive step 6 get written to `docs/decisions/` as an ADR.

**Decisions are provisional until code depends on them.** We build step by step. Do not
treat an ADR as a commitment — treat it as a record of what we knew at the time. Changing
one is normal and cheap; the only expensive thing is changing one without noticing what
we'd traded away for it. Claude should *not* defend a written decision on the grounds that
it's written down. See `docs/decisions/README.md` for the status vocabulary and how to
supersede.

Corollary: **don't decide things early.** If a question belongs to Phase 4, it gets
answered in Phase 4, with the information Phase 3 produced. Park it in
`docs/open-questions.md` and move on.

## The interview layer

Every ADR ends with an **Interview angle** section: how this decision maps to a question
someone would actually be asked ("why not a vector database?", "how do you background a
job?"). Akin should be able to answer these out loud without notes. Claude should
occasionally quiz cold, without warning, rather than only explaining.

## Teaching style

- Concept → why it exists → what it replaces → then code. Never code-first.
- Prefer showing the failure the abstraction prevents over describing the abstraction.
- Name things precisely. `AsyncSession`, `greenlet`, `HNSW`, `ef_search`, `RRF` — the
  vocabulary *is* part of the deliverable.
- When Akin's code works but is wrong-in-a-way-that-bites-later, say so. Don't let it slide
  because it passes.
- Don't hedge. If Celery is the better choice, say Celery is the better choice.

## Repo layout

```
docs/
  00-brief.md            the product brief, verbatim, as agreed
  01-learning-contract.md what Akin is here to learn, and how we'll know it worked
  curriculum.md          concepts mapped onto build phases
  open-questions.md      decisions deliberately not made yet
  decisions/             ADRs — one file per decision, with the options we rejected
  notes/                 Akin's own notes, session logs, "I finally got X"
  examples/              full worked examples Claude wrote on request (never imported by src/)
src/                     the app. Akin's code.
```
