# 0041 — Easter eggs: rare, earned, and never a score

**Status:** active
**Date:** 2026-09-29

## Problem

Akin asked for easter eggs: small surprises that make memo feel like somebody made it.
Two things pull against that:

- **A surprise that happens every day stops being one.** It turns into decoration, then
  into noise.
- **memo deliberately keeps no score.** No streaks, no overdue counts, no "you missed 3
  days" (ADRs 0028, 0029: a person to get in touch with is a suggestion, not a debt).
  Rewards are exactly where apps start to keep score.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| None: productivity apps stay serious | Nothing to maintain, nothing to misfire | The app stays competent and anonymous, which is the opposite of what was asked for |
| Gamification: streaks, badges, a level | The industry's default engagement loop; easy to measure | It *is* a score. A streak turns a capture app into an obligation, and a broken streak into a reason to stop opening it |
| **A few rare moments keyed to real events, plus hidden touches you find by playing** | Delight without obligation; each one means something (a real milestone, a real anniversary, the real season) | Every trigger must fire exactly when it should and only once. A mistimed party is worse than none |

And for where "already shown" lives:

| Option | Buys | Costs |
| --- | --- | --- |
| On the server (a column per moment) | One truth across phones | A migration and an API for something cosmetic |
| **On the phone, in the keychain (`devicePref`)** | No schema; it survives a reinstall, like the other device settings | A second phone would celebrate the same milestone again |

## Decision

**Six touches, each rare or hidden, none of them a count you're measured against.**

Found by playing:

1. **Tap the dot.** On Home, the amber full stop in `memo.` hops when tapped. Five taps
   within three seconds let the season out: a burst from the dot at once, then a swarm
   across the screen. (The first version allowed two seconds and started the swarm above
   the screen, so its first second was hidden behind the status bar and nothing seemed
   to happen where you tapped. Akin reported it as not working.)
2. **Search for the season.** Typing "fireflies", "snow", "leaves" or "petals" (and a few
   synonyms) into search sends them across the screen, once per word typed.

Keyed to real events:

3. **The season in the particles.** Ticking something off sends up whatever the season
   has: fireflies in summer, leaves in autumn, snow in winter, petals in spring. Leaves
   and snow fall rather than rise. The hemisphere comes from the phone's timezone, the
   only hint of place memo has without asking for location. A miss only swaps particles.
4. **Milestones.** The 100th, 500th, 1,000th, 2,500th, 5,000th and 10,000th note get a card
   on Home, a swarm and a glowing dot. The count comes from `GET /v1/me` (`note_count`)
   after a note is saved. **The first time memo sees a count, it records it without
   celebrating**, so this build arriving on a phone with 300 notes doesn't throw a party
   for the 100th. Each milestone is celebrated once, even if deleting notes takes the count
   back under it.
5. **memo's birthday.** On the anniversary of the account, a card: "A year of memo."
   Once on the day. An account made on 29 February has its birthday on 28 February in
   other years.
6. **Late nights.** Between 1 and 5 in the morning, Home's greeting says "Still up,
   Akin?" instead of "Good evening".

**All of it respects Reduce Motion** (no particles; the cards still appear) and
VoiceOver (a card is announced; the dot's tap target is hidden from it, because a
hidden toy isn't a control).

## What we're accepting

- **A second phone celebrates again.** The "shown" state is per phone. Fine for one
  person with one phone; with several it would be a server field.
- **The timezone guess is sometimes wrong.** Someone in the south on a northern timezone
  (or a VPN'd one) gets autumn's leaves in spring.
- **Discoverability is low by design.** Most people will never tap the dot five times.
  That's what makes it an easter egg; nothing important hides there.
- **"Still up, Akin?" could read as judgement** at 2am. It's meant as company. If it
  ever reads otherwise, it goes.

## Revisit when

- memo has users with more than one phone: move the "shown" records to the server.
- Anyone asks for a streak: the answer is this ADR. Streaks are the score this decision
  rejects.
- A moment fires at the wrong time even once: fix the trigger before adding another.

## Interview angle

> **"How would you add delight to a product without it becoming gamification?"**

Tie each reward to something true, not to engagement: a real milestone, a real
anniversary, the actual season. Ration them hard, because rarity is the whole value.
Keep the "already shown" state honest: the first observation is a baseline, not a
crossing, or the first release after the feature ships celebrates everyone's past at
once. That's the backfill problem in miniature, and it shows up in any "first time you
did X" feature. And say what you won't build: a streak is a retention mechanic that
punishes absence, which is the opposite of what a capture app is for.
