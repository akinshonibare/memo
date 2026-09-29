# 0052 — memo's own dialogs, and one Places circle instead of a row of header icons

**Status:** active
**Date:** 2026-09-29

## Problem

Three things Akin raised about how memo feels, after using it:

- **System dialogs everywhere.** The + menu, every delete confirm, renames and pickers
  were iOS's action sheet, alert and text prompt: grey, unbranded, no icons, and the
  one part of memo that didn't look like memo.
- **Header icons stacking up.** Planner, Review, People, Faith and Habits were five
  circles in a row at the top right, growing with each feature, naming nothing.
- **Nothing says where something new went.** A note's todo lands in the Planner, a
  prayer in Faith, a day in Habits, silently. And after saving a note you were left
  on Home, not shown what memo took from it.

## Options

**Dialogs**

| Option | Buys | Costs |
| --- | --- | --- |
| Keep the system ones | Free accessibility and familiarity | Looks like another app; no icons; Android and iOS differ |
| A library (react-native-actions-sheet, etc.) | Ready-made | A dependency for four small components; still needs memo's styling |
| **Our own: `choose`, `confirm`, `ask`, `tell`** | memo's colours and type, an icon per choice, one call on both platforms | We own focus, VoiceOver and keyboard handling; iOS won't present over a sheet, so a host lives in every sheet |

**Navigation** (sketched for Akin, who wanted icons, not labels, and picked the last)

| Option | Buys | Costs |
| --- | --- | --- |
| Labelled chips under the greeting | Every place named | Text, which Akin didn't want; a line of height |
| A tab bar | The platform default | Fights the Ask bar and record button for the bottom |
| **One circle above the record button that opens upward into a column of icons** | A clean header; places by the thumb, beside the other actions | One more tap; icons must be learnt |

**Something new**

| Option | Buys | Costs |
| --- | --- | --- |
| Unread counts per place | Precise | A score to clear, which memo refuses (ADR 0041) |
| **A mark: the circle breathes an amber ring; open, the place's icon pulses with a dot** | Says where to look, nothing to clear but a visit | Needs to know what's new and what you've seen |

## Decision

- **`src/dialogs.tsx`**: `choose` (a menu with icons), `confirm` (a destructive yes/no,
  its yes spelled out), `ask` (one line of text; `allowEmpty` where clearing is valid)
  and `tell`. A `<Dialogs />` host sits in every sheet and over the app; only the most
  recently mounted draws, like the lock cover (ADR 0035). No system action sheet,
  alert or prompt is left in the app.
- **The Places circle** (`screens/Places.tsx`): a compass circle the size of the +
  button, above the record button, opening into Planner, Review, People, Habits and,
  with Faith on, Faith. Habits always shows (its screen says when it's off). The page
  dims behind it; a tap outside closes it.
- **`GET /v1/news`**: the newest item in each place (planner: todos, events, ideas and
  pending idea threads; review: items waiting; people; faith: prayers, passages,
  "answered?", only with Faith on; habits: days counted). The phone keeps when you last
  opened each place (any route into it counts) and marks those with something newer;
  the first launch counts everything as seen. With Reduce Motion, dots only.
- **A saved note opens at once**, so you watch its items arrive (its page already
  polls). One kept offline stays on Home.
- **The map is out of the app** (Akin: not good enough yet). `GET /v1/map` and ADR 0049
  stay for a better design.

## What we're accepting

- **Our dialogs must stay accessible by hand**: `accessibilityViewIsModal`, the escape
  gesture and labels are set, but a system update won't improve them for us.
- **Icons without labels** rely on learning five glyphs; VoiceOver reads the names.
- **"New" is per phone.** A second device has its own idea of what you've seen, and a
  clock far off the server's could mark or miss one.
- **Opening the note after each save** is one more screen to back out of when you're
  capturing several things in a row.

## Revisit when

- A place is added: the column grows by one; past six, group them.
- Akin finds the auto-open annoying for quick captures: make it a Settings switch.
- A new map design is ready (ADR 0049).

## Interview angle

> **"Would you replace the platform's native dialogs with custom ones?"**

Only with a reason and a budget for what you lose. Native dialogs give accessibility,
consistency and zero maintenance; custom ones buy brand, icons and cross-platform
consistency, and cost you focus management, screen-reader semantics and presentation
rules (iOS presents one modal at a time, so a custom dialog has to be drawn inside
whatever sheet is showing). Keep the API identical in shape (`await choose(...)`) so
call sites stay simple and swapping back stays cheap.
