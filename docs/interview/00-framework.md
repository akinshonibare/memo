# Delivery framework (Hello Interview)

Source: https://www.hellointerview.com/learn/system-design/in-a-hurry/delivery

The framework exists to stop the #1 failure mode: **not delivering a working system**,
usually mislabelled as "time management". Structure buys you a track to run on.

| # | Step | Time | Produces |
| --- | --- | --- | --- |
| 1 | **Requirements** | ~5 min | Top-3 functional ("users should be able to…") + top 3–5 non-functional, **quantified** |
| 2 | **Core Entities** | ~2 min | A bulleted list of nouns. First draft, deliberately incomplete |
| 3 | **API / System Interface** | ~5 min | The contract. REST by default. Plural resource nouns |
| 4 | **Data Flow** *(optional)* | ~5 min | Ordered list of processing steps. **Applies to memo** — it's a data-processing system |
| 5 | **High Level Design** | ~10–15 min | Boxes and arrows that satisfy the API, endpoint by endpoint |
| 6 | **Deep Dives** | ~10 min | Harden against the non-functional requirements; bottlenecks, edge cases |

## Rules that trip people up

- **Requirements:** prioritise to the top 3. A long list *hurts* you. FAANG rubrics
  explicitly score the ability to focus on what matters.
- **Non-functional must be quantified.** "Low latency" is worthless. "Search under 500ms"
  is a requirement. Name *which part* of the system needs the property.
- **Skip capacity estimation** unless a number will change the design. Say so out loud.
  Do the math later, at the point it decides something.
- **Core entities are a first draft on purpose.** You don't know what you don't know.
  Fields get added during high-level design, next to the database box.
- **Derive the current user from the auth token**, never from a request body or path param.
- **Don't layer complexity in step 5.** Note the cache/queue verbally, move on, do it in
  deep dives. Candidates who add complexity early never reach a complete solution.
- **Don't talk over the interviewer in deep dives.** They have specific signals to collect.

## Assessed on

**Problem Navigation** (usually the decider) · **Solution Design** ·
**Technical Excellence** · **Communication and Collaboration**

Interviewers actively probe for memorised answers — expect them to doubt a correct answer
to see whether you can defend it.

## Seniority calibration

Mid-level: covers the basics competently. Senior: moves through basics fast, **leads** the
deep dives unprompted rather than waiting to be probed.
