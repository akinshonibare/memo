# Cold quiz bank

Questions Claude asks **without warning**, at the start of a session, before any work.
One question. No notes, no scrollback, answered out loud.

A wrong answer is data, not a failure — it becomes that session's topic. The point is to
find out which explanations are memorised shapes rather than understood mechanisms, and the
only way to find that out is to ask when Akin isn't braced for it.

⚠️ marks a question Akin has already got wrong or missed. Those come round more often.

---

## Requirements and scoping

1. ⚠️ You're asked to design memo. Give me your non-functional requirements, quantified.
   *(Missed durability entirely on the first attempt.)*
2. ⚠️ Why is durability the requirement that leads for this system, and what makes it
   different from a social feed?
3. ⚠️ Your ask endpoint has a latency requirement. State it. Why is it two numbers and not
   one?
4. What's the first sentence out of your mouth when handed an ambiguous design prompt, and
   why does it come before anything else?
5. Why does the guide tell you to skip capacity estimation up front, and what would make
   you do it anyway?
6. You cut the offline requirement. What did that cost you, and what now justifies the
   async pipeline?

## Entities and schema

7. ⚠️ Is a chunk a core entity or an implementation detail? Defend it.
8. Why is `user_id` denormalised onto `chunks` and `derivations` when it's reachable
   through `capture_id`?
9. What do `extractor_version`, `embedding_model`, and `client_token` each buy you, and
   which of them is impossible to retrofit?
10. Name the entity you'd sacrifice first if an interviewer says your list is too long.

## Async Python

11. What does `await` actually do? Not what it's for — what happens.
12. Why does SQLAlchemy 2.0 async need `greenlet`?
13. What goes wrong if two coroutines share one `AsyncSession`?
14. You put `requests.get()` in an async handler. Describe the failure, precisely, to
    someone who thinks it only slows down that one request.
15. What does lazy-loading do under async, and why is it a trap rather than a bug?

## Background work

16. Why is "exactly once" a lie, and what replaces it?
17. Make a retry safe. Where is idempotency enforced, and why not in application code?
18. Why jitter?
19. A capture fails extraction three times. Walk me through what the user sees.
20. Why not Celery?

## Retrieval

21. What is an embedding, geometrically, and what is it lossy about?
22. Why does cosine similarity fail on proper nouns?
23. Explain RRF in one sentence. Then explain why it beats normalising scores.
24. HNSW vs IVFFlat. What do `m`, `ef_search`, and `lists` control?
25. Why chunk at all? What does chunk size trade off?
26. Why is a citation a retrieval artifact and not a generation artifact — and what does
    that buy you?
27. Your ANN query has `WHERE user_id = ?`. Why is that the hard case for HNSW?
28. Do you need a vector database? Give the real reason, not the performance one.
40. "What's on this week?" returns nothing useful from your RAG system. Why can't
    similarity search answer it, what's the textbook fix, and why did memo not use it
    (ADR 0045)?
41. You filter a vector search to one weekend's notes and get two results back when
    there are twenty. What happened, and what do you do instead of widening
    `ef_search`?
42. "When did I last see Tunde?" keeps citing an older note. What kind of question is
    "last", and where should the answer come from?

## LLM engineering

29. What is a structured output, mechanically? Name three ways to get one and how each
    fails.
30. The model returns something that doesn't validate. Now what?
31. What's an eval, and why isn't "it looked good in testing" one?
32. For extraction, which do you optimise — precision or recall? Justify it.
33. Multi-turn broke retrieval. Why, and what's the fix, and what does the fix cost?
34. What do you log per LLM call and why is cost a first-class metric?

## The system as a whole

35. A user's private journal appears in someone else's answer. How did it happen, and which
    of your three isolation layers failed?
36. Why is your observability deliberately thin, and what is doing that job instead?
37. What breaks first at 10× and what do you do about it?

## Entity resolution (ADR 0029)

38. Two notes say "Tunde". How does memo decide whether they're the same person, and
    which mistake is it built to make instead of the other?
39. "Last contacted" is computed on every request instead of stored on the person. Why?
    And why does resolving a new name need a lock when nothing else in the pipeline does?
43. A new note says "got the offer!" and an old prayer is about the job. Walk through how
    memo connects them, why similarity alone isn't enough, and why it asks instead of
    marking the prayer answered (ADR 0048).
44. Why is memo's map an ego-graph and not a graph of everything? What does it give
    up (ADR 0049)?
45. The habits reader copies "3 miles" as value 3, unit "mi", and code converts it.
    Why not ask the model for kilometres? And which error does the eval hold to the
    highest bar for amounts, and why (ADR 0050)?
46. memo syncs Apple Health by sending a whole 30-day window each time. How does that
    handle a workout deleted in Health, two syncs at once, and a day the user removed
    in memo? When would you switch to anchored queries (ADR 0050)?
47. Two parts of an iOS app want the microphone at once: a recorder and a live
    transcription stream. What goes wrong with the audio session, and what did memo's
    patch change (ADR 0051)?
48. memo replaced the system action sheet and alert with its own. What do you lose,
    and why does its dialog host have to be drawn inside every sheet (ADR 0052)?
49. Deleting a note now deletes its items through `ON DELETE CASCADE` rather than in
    the route. Why the database? And what premise changed from ADR 0022 (ADR 0053)?
50. A voice note's upload fails and the person taps End again; the server answers 422
    to every retry. Why does a `client_token` not save you here, and what has to be
    true of a retry for it to? Then: the error said "upload failed", nothing more.
    Which change comes first and why (ADR 0071)?

---

## Quiz log

Record cold-quiz results here. Question number, date, verdict, what to revisit.

| Date | Q | Verdict | Follow-up |
| --- | --- | --- | --- |
| 2026-08-18 | 2 | **Miss (partial)** | Got "vital vs non-vital info". Missed the mechanism: irreplaceability (memo is the only copy; a feed item is redundant and regenerable), the availability-vs-durability inversion, and every design consequence (ack-before-process, keep raw forever, idempotency key, visible dead-letter). Re-ask. |
| 2026-08-18 | 2 (re-ask) | **Partial** | Improved: trust, and loss being silent. Still missing the mechanism — *irreplaceability*, not noticeability (a dropped feed item is redundant; the user holds another copy). Missed the availability/durability inversion (prefer unavailable over lossy; the ack transfers custody). Named no design consequences. Re-ask once more after #13/#18/#19 land. |
| 2026-09-27 | 7 | **Miss** | Answered "core entity" with no defense, then asked for the answer outright rather than reasoning it out. Missed: chunk has its own versioned lifecycle (embedding model + chunking strategy) independent of its parent, is the unit of retrieval and citation, and needs a stable id precisely because embeddings are expensive to persist and must survive a rolling re-embed. Re-ask later. |
