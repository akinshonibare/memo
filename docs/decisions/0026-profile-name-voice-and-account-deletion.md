# 0026 — A profile: the name the assistant uses, a voice, and deleting the account

**Status:** active
**Date:** 2026-09-28

## Problem

Settings had grown out of developer needs: a raw timezone field, the API's URL, and
buttons. Akin asked for a professional profile page, and specifically for a name the
assistant uses for him. Three questions hide in that request:

1. Where does the name live, and how does it reach the model without costing prompt
   caching or opening an injection path?
2. What does a product owe a user on a settings page? The industry answer includes
   **account deletion**, which Apple *requires* in-app for any app offering Sign in with
   Apple (App Store guideline 5.1.1(v)).
3. What happens to the API URL, which a person should never see?

## Options

**The name.**

| Option | What it buys | What it costs |
| --- | --- | --- |
| **In the system prompt** | Simplest to write | The system prompt stops being byte-identical across users, which ends cross-user prompt caching. It also mixes user-controlled text into the highest-authority instruction slot |
| **In the user turn, one line before the notes** | The system prompt stays static and cacheable. The name sits with the other per-request context, at user-level authority | One more line per question |
| **Only in the app** (greeting text, no model involvement) | No prompt change | Doesn't do what was asked: the assistant still says "you" |

**Account deletion.**

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Soft-delete (disable the account, purge later)** | Undo window | The data still exists, which is the thing the user asked to remove, and every query has to respect a flag. The same argument ADR 0022 made against soft-deleting notes |
| **Hard delete now**: the existing purge, then sessions, then the user row | What the user asked for, immediately. Reuses the delete-everything path | No undo. Two confirmations and a typed DELETE stand in for one |

## Decision

- **Name**: `users.display_name` (≤60 characters, cleaned to one printable line). It's
  seeded from Apple's full name on the only sign-in where Apple sends it, and never
  overwrites a chosen name. It reaches the model as the first line of the user turn
  (`answer-v2.2`).
- **Voice**: `users.voice`, one of six vetted Aura-2 voices, with an audible preview when
  choosing. The speak route uses it unless a preview names another.
- **Account**: sign out, **sign out of all devices** (revokes every session), and **delete
  account**: a hard delete that runs the shared purge, then sessions, then the user row.
- **Plumbing**: the API URL leaves the UI. It's reachable only through a developer panel
  (tap the version seven times), and on the sign-in screen only in development builds.

## What we're accepting

- **The Apple token isn't revoked on deletion.** Apple asks apps to call its REST revoke
  endpoint, which needs the team's Sign in with Apple private key on the server. Until
  that is set up, signing in again after deleting creates a fresh, empty account rather
  than being refused. That's harmless for data, and required before any App Store
  submission.
- **A user-supplied string now enters every answer prompt.** It's cleaned and capped, and
  it sits at user-turn authority, where the user already has full control of the question.
  So it adds no capability that asking didn't already have. Still, it's the first
  profile-sourced text in a prompt.
- **Usage shows AI spend only.** Deepgram (transcription, speech) is logged but not in
  `llm_calls`, so "last 30 days" undercounts voice-heavy use. It's labelled as AI calls,
  not total cost.

## Revisit when

- Before TestFlight or the App Store: add Apple token revocation to account deletion.
- Voice becomes a meaningful share of spend: give speech its own usage rows so the
  number on the profile is the whole bill.
- Someone asks to be addressed differently in speech than in text (a nickname, a
  pronunciation): that's a second field, not a longer first one.

## Interview angle

> **"Personalize the assistant with the user's name. Where does it go?"**

Not the system prompt. That breaks prompt caching across users, because the prefix
stops being identical, and it puts user-controlled text into the highest-authority slot.
It goes in the user turn as context, cleaned to one printable line and length-capped,
where it carries no more authority than the question itself. The follow-up I'd raise
unprompted: a settings page in a real product owes the user account deletion, and Apple
requires it for Sign in with Apple apps. I'd hard-delete, because soft-deleted personal
data is still personal data. The deletion code clears an explicit list, in foreign-key
order, and its test finds every table with a `user_id` column from the schema itself and
asserts each is empty. A table added later fails the test instead of silently surviving
deletion.
