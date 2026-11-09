# 0072 — A tab bar with the record button in the middle, four tabs, everything else pushed above it

**Status:** active
**Date:** 2026-11-08

## Problem

Akin (2026-11-08): *what options do we have to change how we do the tab, I actually
don't like it.* "The tab" was the Places circle (ADR 0052): a compass above the record
button that opened upward into a column of icons for Planner, Review, People, Habits,
Lists and Within.

Three things were wrong with it, and one thing was right:

- **Icons, no labels.** Six glyphs to learn before the app made sense.
- **Height.** Six places made the open column about 570pt, most of a phone.
- **Two taps and a scrim** between you and any section, every time.
- Right: Home is capture first. Record, write and Ask owned the bottom edge, and the
  sections were deliberately secondary.

ADR 0052 had said "past six, group them". Lists (ADR 0069) made six.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Keep the compass, open a labelled grid sheet instead of the column | Labels; fits any screen; the bottom bar untouched; no navigation change | Still two taps and a modal. Akin ruled it out |
| A labelled strip of pills at the top of the note list | One tap, labels, the dots; an afternoon's change; ADR 0033 untouched | At the top, far from the thumb; scrolls away with the list; six pills don't fit a phone width, so two are always off-screen; it is the header row ADR 0052 moved away from, with labels |
| **A bottom tab bar** (`@react-navigation/bottom-tabs`, the iOS default) with record, + and Ask stacked above it on Home | One tap from anywhere, always visible, labels, badges; each tab keeps its state | Two bars at the bottom of Home and a shorter note list; the capture controls shrink; sections become co-equal with capture |
| **The same tab bar with the record button set into the middle of it** | Everything the tab bar buys, and record stays the biggest thing on screen, under the thumb | Ask and + lose their bottom-bar seats; a custom tab bar instead of the stock one; four tabs, so six destinations have to be grouped |
| Fewer destinations (fold Review into Planner, Lists and Habits and Within into one screen) | Whatever control is kept has 3–4 entries | A product change; some screens lose their own identity. Not an alternative to the others so much as what the tab bar forces |
| A horizontally paged Home | — | Rejected: rows already swipe sideways for their own actions (ADR 0033) |

## Decision

**A bottom tab bar with four tabs, Home, Planner, People and More, and the record button
set into the middle of it. Everything else stays a screen pushed on the native stack
above the tabs, covering the bar.**

- **The navigators** (`App.tsx`): a root native stack whose first screen is the tab
  navigator; Detail, Person, List, Habit, Ideas, Review, Lists, Habits and Faith are
  pushed on the root stack, as before. A pushed screen covers the bar, and back retraces
  the path you took (ADR 0033). `memo://` links, the share sheet and notification taps
  reset to the tabs and push from there; nothing about their parsing changed.
- **The bar** (`screens/TabBar.tsx`): memo's own, not the stock one, because the stock
  bar has no slot for a button that rises above it. Icon and label per tab, the
  selected one in text colour, and the record button (64pt, the same ring as before)
  lifted 14pt above the bar's top edge. Tapping record opens the record sheet for an
  ordinary note, wherever you were.
- **Marks** come from the same `GET /v1/news` as before (ADR 0052), now through a
  `NewsProvider` so one fetch serves the bar and More. A tab carries a dot when any
  place it speaks for has something new: Planner for planner and review, People for
  people, More for habits and faith. A mark, not a count (ADR 0041). Looking at the
  place still clears it, however you got there.
- **Home** keeps the notes, Today and the search field. Ask and + share one pill that
  rides above the tab bar (Ask is the pill, + sits in its right end), clear of the
  record button rising out of the bar, with the old fade under it so the list runs
  beneath. The first cut (2026-11-08) had put them in the header row beside the
  avatar; Akin's first reaction to the shipped build was that the header is the far end
  of the phone from the thumb, which was the whole complaint about Places. Moved the
  next day. The compass is gone.
- **Planner** gains a Review button in its header beside Ideas: the review is about
  the week, so it lives with the days.
- **More** (`screens/MoreScreen.tsx`) holds Lists, Habits and, with Faith on, Within:
  three labelled rows with a line each and a dot where something is new.
- **Planner and People** render no back button when they are tab roots (`onBack` is
  optional); they keep it if ever pushed.

## What changed

ADR 0052 chose "one circle above the record button that opens upward into a column of
icons" over a tab bar because a tab bar "fights the Ask bar and record button for the
bottom", and Akin wanted icons, not labels. The second premise reversed: after six weeks
of use the icons were the complaint. The first was real and is answered differently: the
record button joins the bar instead of fighting it, and Ask and + move up. The
reasoning was right for a five-place app with a user who had not yet lived with it; it
was overtaken, not mistaken. The dialogs, the saved-note auto-open and the rest of 0052
stand.

## What we're accepting

- **Four tabs means grouping.** Lists, Habits and Within share More, one tap further
  than the others. "More" is the honest name for a hub; if one of its rows becomes a
  daily visit, it should take a tab of its own and the grouping should be redone.
- **Two bars at the bottom of Home**: the Ask pill and the tab bar, about 100pt
  together, so Home's list is shorter than any other tab's. Ask is Home-only; from
  Planner or People it is a tab away. The alternatives were hiding Write, Photo and
  Ask behind a long-press on record (nothing on screen, nothing discoverable) or one
  bottom field that searches as you type and asks on return (fewer inputs, but a
  product change to make once the tab bar has been lived with).
- **Sections are co-equal with capture in the layout.** The centre button is what
  keeps memo reading as a capture app; if it ever shrinks to a plain tab, the thesis
  has quietly changed.
- **A custom tab bar** is ours to keep accessible (roles, selected state, labels that
  say "something new") and ours to keep in step with React Navigation's `tabPress`
  events.
- **Pushed screens cover the bar.** Each tab keeps its own screen's state, but not a
  stack of pushes: open a person from People, switch to Planner, and that person is
  gone from the path. Stacks-inside-tabs would keep them and was not worth the extra
  model today.
- **About 56pt plus the home indicator** at the bottom of every tab, gone from the
  content.

## Revisit when

- A row in More is opened most days: promote it, and regroup.
- Someone wants to switch tabs with a note or a person open: that is stacks inside
  tabs, and a real reason to move the pushed screens into each tab's own stack.
- A sixth destination appears: the bar is full, and the answer is grouping again, not
  a fifth tab beside the record button.
- The record button is used less than any tab: the product has become a reader rather
  than a recorder, and the layout should say so.
- Akin switches to Home just to reach Ask: put Ask in the bar on every tab, or merge
  search and Ask into one bottom field.

## Interview angle

> **"How would you add a tab bar to an app whose main action already owns the bottom edge?"**

Decide first whether the main action is still the main action. If it is, it goes in
the bar, not above it: a raised centre button keeps it the largest target and under the
thumb, and the sections take the four slots around it. Then decide the navigator shape:
a root stack with the tabs as its first screen keeps detail screens covering the bar and
keeps back retracing the path; stacks inside tabs keep a path per tab at the cost of a
more complex model and deep-link handling. Human Interface Guidelines say three to five
tabs, so a sixth destination forces a grouping decision, which is a product question
you should make on purpose rather than hide behind a "More" you never revisit. Badges:
a mark, not a count, if the app has decided it will never show a score.
