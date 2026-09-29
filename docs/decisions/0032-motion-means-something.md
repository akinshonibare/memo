# 0032 — Motion: Reanimated 4, a vocabulary that pairs with the haptics, and a living full stop

**Status:** active
**Date:** 2026-09-29

## Problem

Akin asked for fun, distinctive animations. The app had none: screens cut, rows appeared
and vanished, and the record screen had three static dots. Two constraints:

- **Motion that means nothing is noise.** The haptics already have a vocabulary
  (`haptics.ts`: tap, press, done, pick, warn). Motion should mean the same things.
- **Home re-renders every 3 seconds** while anything is processing. An animation on the
  JavaScript thread would stutter exactly when there's something to watch.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| React Native's `Animated` / `LayoutAnimation` | No dependency | Runs on the JavaScript thread (native driver only for transform and opacity); stutters under Home's polling; weak layout animations |
| **Reanimated 4 + Gesture Handler** | UI-thread worklets, layout animations (`entering`, `exiting`, `LinearTransition`), Reduce Motion built in, swipeable rows; the industry default | Two native modules (a new build); React Compiler lint rules about shared values to follow (`.set()`, not `.value =`) |
| Skia | GPU canvas and shaders, for truly organic shapes | A large native module for one or two effects; a second rendering model to learn |
| Lottie | Designer-made animations | No designer; files to maintain |

## Decision

**Reanimated 4 and Gesture Handler. Every movement belongs to a small vocabulary
(`src/motion.tsx`), each paired with a haptic:**

| Haptic | Motion |
| --- | --- |
| tap | the control squeezes (`PressableScale`) |
| press | it swells (the record button, the orb) |
| done | it lands and settles: a checkbox pops and overshoots once; the buzz fires on the landing, not the touch |
| pick | a marker glides to the choice (week strip, segmented controls) |

**Four signature moments, one per screen:**

1. **The living dot.** The amber full stop in `memo.` is the app's status light. It
   breathes while a note is processing or waiting to sync. The brand mark and the
   system state are one thing.
2. **The voice orb.** It replaces the static dots and follows the microphone's real
   level (`isMeteringEnabled`, dBFS mapped to 0..1). When the note is saved, it shrinks
   into a full stop and falls toward the list.
3. **Ink-in.** When a note's words first arrive, its title writes itself in word by word.
   It plays once, on the transition, then it's a plain `Text` again, so truncation and
   selection behave as before.
4. **Fireflies.** Ticking a todo sends a few amber specks up from your finger. Clearing
   the last thing on Today, or the week's "Still open", sends a swarm. Forest plus amber
   makes fireflies.

**Also:** rows spring into place when others come or go, a theme change fills the
Settings sheet from your finger in the new colour, and Ask shows a ripple while
listening and moving bars while speaking.

- **Springs, not durations,** and every spring names its mass. Reanimated 4's defaults
  (mass 4) are slow and wobbly.
- **With Reduce Motion on,** Reanimated snaps springs and layout animations to their
  end. The orb's wobble, the breathing, the fireflies and the ink-in don't run at all.

## What we're accepting

- **A new native build** before any of it reaches the phone. Every later tweak can ship
  over the air (ADR 0037).
- **The orb is an approximation of a blob.** It's four animated corner radii and two
  drifting halos. Skia would do a real metaball; not worth a new renderer.
- **Ink-in lays the words out as separate views while it plays,** so line breaks can
  differ slightly from the final text for the second and a half it runs.
- **More to keep honest.** An animation that fires on the wrong event is worse than none.
  Each one is keyed to a state transition, not to a render.

## What changed (2026-09-29): no layout animations in Home's list

On the phone, Home showed a note-sized gap under "Notes" after a note was deleted,
until something else moved the list. Home is a virtualized `FlatList`, and "rows spring
into place when others come or go" was Reanimated's `itemLayoutAnimation`: each row is
moved to its new place by a transform. A transition that starts while Home is covered
(a note deleted from its own page) or is interrupted can leave those transforms where
they started - every row below still drawn in its old place. It didn't reproduce in a
debug build in the simulator, which is the point: it depends on timing.

The original reasoning wasn't wrong so much as incomplete: layout animations are fine
in a plain view (Today, the Planner, the review still use them) and fragile in a
virtualized list, where cells are laid out and recycled by the list, not by us. So Home
no longer animates positions at all. A deleted row fades where it is, then leaves, and
the list closes up by ordinary layout; new rows still fade in. Nothing can be stranded,
because nothing is moved by a transform. What we gave up: the list closing up is
instant rather than a spring.

## Revisit when

- An effect needs real GPU work (shaders, a real metaball): Skia, for that component only.
- Someone reports motion sickness despite Reduce Motion: audit the loops first.

## Interview angle

> **"How do you add animation to a React Native app without making it janky?"**

Keep it off the JavaScript thread. Reanimated runs animations as worklets on the UI thread,
so a list re-rendering every few seconds doesn't drop frames. Key every animation to a
state change (a note's words arriving, a todo completed), not to a render, or it replays
on every poll. And give motion a vocabulary, the way you'd give colour one: the same
movement always means the same thing, paired with the same haptic. Respect Reduce Motion
by default, and treat decorative loops as optional.
