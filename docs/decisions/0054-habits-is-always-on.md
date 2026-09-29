# 0054 — Habits is always on

**Status:** active
**Date:** 2026-09-29
**Supersedes:** [0043](0043-habits-noticed-not-logged.md)'s Settings switch (the rest of 0043 stands)

## What changed

ADR 0043 made Habits a switch, off by default, because reading every note for what you
did costs about a cent a note, and not everyone wants habits. Akin, using it: *only
Faith should be enabled or disabled, not Habits.* Habits is part of what memo is for
him; Faith is a space some people want and others don't. The cost reasoning was right;
the product call moved.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Keep the switch (0043) | Cost only for those who want it | A setting Akin doesn't want; a feature hidden until found |
| **Always on, the column kept as an operator's switch** | Nothing to set; the column can still stop the spend for one account | Every note of every account is read: about a cent each |
| Always on, the column dropped | Least code | No way to stop it for one account short of a deploy |

## Decision

**Habits is on for every account (migration 0023 switches existing ones on and makes
new ones start on) and no setting turns it off.** Settings loses the switch; its
Habits group now holds only the Apple Health switch. `PATCH /v1/me` still accepts
`habits_enabled: true` as a no-op, so an app built with the switch keeps working, and
refuses `false`. The column stays: an operator can set it false for one account, and
every gate that reads it (the habits stage, the backlog sweep, the Health sync, Today's
habit line) still honours it.

## What we're accepting

- **About a cent per note for everyone**, plus a one-off read of the last 60 days for
  any account that had Habits off when this ships.
- **No way for a user to opt out.** Fine for Akin; with other users, that would be a
  question to reopen, not to assume.
- **Two notions of "off":** the old user switch is gone, but code still says "while
  Habits is on", because the operator's switch uses the same column.

## Revisit when

- memo has users who don't want habit tracking: a user setting again, off by default
  or on.
- The per-note cost matters: a smaller model for the habits reader (0043's trigger).

## Interview angle

> **"When do you remove a user setting?"**

When nobody should be choosing: a setting is a question you make every user answer,
and a feature behind an off switch is a feature most people never find. Keep the
control where it's still needed, though: here the column stays as an operator's switch,
because the reason the setting existed (cost) hasn't gone away, only who decides.
