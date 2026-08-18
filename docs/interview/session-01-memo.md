# Session 01 — critique log

Date: 2026-08-17 · Framework: [00-framework.md](00-framework.md)
The design itself lives in [memo-system-design.md](memo-system-design.md). **This file is
the learning record**: what the first attempt got wrong at each step, and why it mattered.

Format: Akin answers each step cold, Claude reviews before moving on.

---

## Step 1 — Requirements

**Biggest miss:** durability was absent. For a personal journal it's the requirement that
should *lead* — there's no upstream copy and no "the user will just write it again."

**Second miss:** scope and scale were never stated. Three "users should be able to…"
statements with no mention of how many users. The interpretation was implicitly
single-user, but silently — so an interviewer can't tell deliberate scoping from oversight.
This is the classic Problem Navigation ding.

| First attempt | Problem |
| --- | --- |
| "input/edit/delete data via text or audio" | Bundles the product thesis (zero-friction capture) with CRUD table stakes. Also "edit an audio capture" is undefined — the audio, or the transcript? |
| "ask questions about past data" | Citations missing. They constrain schema and prompt design, so leaving them out of the requirement drops them from the design |
| "Events (todos, ideas, lists) extracted" | Category name collides with a member name — "event" is also one of the extraction types. Also drops the review/correction step the brief calls non-negotiable |
| "available without internet" | Aimed at offline *read*. Offline *write* is the product-critical half: the moment you most need to capture is the moment you have no signal. Later cut entirely, correctly |
| "respond ≤ 1 sec" | Quantified — good instinct — but ambiguous between time-to-first-token and time-to-complete. ≤1s for a complete generated answer isn't achievable; ≤1s TTFT is. Knowing to split them is the signal |
| "large amount of text, 1 min audio" | An input limit, not a non-functional requirement. And "large amount" is unquantified — exactly what the guide warns against |

**Calibration:** functional requirements were mid-level solid — three of them, roughly the
right three. Non-functional is where ground was lost: one misaimed, one ambiguous, one that
isn't an NFR, and the most important one missing.

**Pattern to watch:** the failures clustered in non-functional requirements, and
specifically in *not asking what breaks if this property is absent*. Durability, isolation,
and quality bars all surface from that question. Try running it explicitly next time.

---

## Step 2 — Core Entities

_(pending)_

---

## Step 2 — Core Entities

**Biggest miss:** dropped `Chunk` — and then spent the word on audio (`AudioChunks`). In a
RAG system `chunk` is a term of art for an embedded unit of text; reusing it for audio
segments is the worst name collision available here. The probe ("is every one of these a
core entity, or is one an implementation detail?") went unanswered.

**Structural error:** `Content` and `AudioChunks` were modelled as siblings — two parallel
things a capture might be. That reintroduces the modality split ADR 0008 exists to prevent.
Audio *does* deserve its own entity (object storage, separate lifecycle, V2 images), but as
an **attachment to** a capture, not an **alternative to** one.

**Naming:** two of five entities were named after their category — `Content` and
`ExtractedData`. Both are the noun equivalent of `temp`.

| First attempt | Problem |
| --- | --- |
| `Content` | Category noun, and circularly defined: *"content (text and transcribed content)"* |
| `AudioChunks` | Collides with the RAG term of art; "chunks" implies V1.5 streaming segmentation |
| `ExtractedData` | "Data" carries no information |
| `Conversations` | Good catch — but it's two entities (`Conversation` + `Message`) and it exceeded the stated FR2, which said single-shot Q&A |
| *(absent)* | `Chunk` — required by the citations requirement |

**Good calls:** spotting `Conversation` at all (it wasn't in the brief); reaching for a
polymorphic association on the extraction type unprompted; `User`.

**Decision made:** multi-turn conversations are in. FR2 amended accordingly, and three
deep-dive debts logged (query rewriting, context management, cost per turn).

**Pattern to watch:** the Step 1 failure was *not asking what breaks if this is absent*.
The Step 2 failure is adjacent — **not tracing each entity back to a specific functional
requirement**. `Chunk` was missing because nothing forced the question "what does a citation
point at?" Run the trace explicitly next time: every entity earns its place by naming the
requirement it serves, and every requirement should be able to name its entities.
