# 0033 — Navigation: React Navigation's native stack, not Expo Router

**Status:** active
**Date:** 2026-09-29

## Problem

Screens were a `view` state in `App.tsx`: a union with a hand-rolled `back` chain. Moving
between screens was an instant cut, with no iOS push and no edge swipe back. Every iPhone
user reaches for that gesture, and it did nothing.

`apps/mobile/AGENTS.md` (from Expo's template) says to use Expo Router.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Keep the view switch and animate it with Reanimated | Smallest change | No interactive swipe back unless rebuilt by hand; still a hand-rolled history |
| **React Navigation 7, `@react-navigation/native-stack`** | Real `UINavigationController` pushes and the edge swipe, from `react-native-screens`; the history is the stack; the `memo://` link handling, the sheets and the share-intent flow stay exactly as they are | Screens stay mounted under the one on top (so they can go stale, and keep polling); one more dependency family |
| Expo Router (file-based, on top of React Navigation) | What `AGENTS.md` asks for; deep links map to routes | It takes over incoming URLs. `memo://record`, `memo://capture?text=…` (Siri, the Action Button, widgets) and the share extension's own URL would need a `+native-intent` rewrite, and those flows are exactly what broke TestFlight builds before. It also restructures every screen into `src/app/` files and moves props into route params and context. |

## Decision

**React Navigation 7's native stack, with the existing screens as render-callback
children, so they keep their props.**

- The stack is the history: a note pushed from a person, then that person's other note,
  then back, back.
- Where the old code replaced a view, it now calls `replace`: a related note replacing
  this one, a merged person replacing the one that no longer exists.
- **Edge swipe only** (`fullScreenGestureEnabled: false`), because rows now swipe
  sideways for their own actions.
- **Mounted-underneath screens:** Home only polls while focused. Screens that can go stale
  underneath (note, person, review, planner) re-read when they come back into view
  (`useRefetchOnFocus`). A person pruned while underneath is removed from the middle of
  the stack, since `goBack` from a screen pops that screen.
- **Links, the share intent and the notification taps** reach screens through a
  navigation ref, and wait (the existing `pending` pattern) until the navigator is ready
  and the app is unlocked.

## What we're accepting

- **A deliberate deviation from `AGENTS.md`.** It's a template default, not a decision
  made for this app. The cost of following it is putting the quick-capture paths at risk.
- **Screens stay alive underneath.** More memory than before, and one more thing to
  remember: a screen can't assume it was just mounted.
- **Render-callback screens skip React Navigation's memoisation.** Fine at this size.

## Revisit when

- The app needs web or shared URLs for its screens: Expo Router's file routes earn their
  keep then. Migrate the quick-capture links into `+native-intent` first, behind a test.
- Screen count grows past about 15, or props threading gets painful: move `api` and the
  callbacks into context.

## Interview angle

> **"Why didn't you use the framework's recommended router?"**

Because a recommendation is a default, not a decision about your app. Here the router
would own URL handling, and the app's most important entry points (Siri, the Action
Button, widgets, the share sheet) are URLs with their own parsing and a history of
breaking releases. The native stack gave the actual user-facing win, real pushes and
swipe back, without touching those paths. I wrote down when that changes: the day screens
need addressable URLs of their own.
