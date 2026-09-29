# 0031 — Themes are data: two hand-tuned Forest palettes, the rest derived in OKLCH behind a contrast check

**Status:** active
**Date:** 2026-09-29

## Problem

Akin asked for three things: respect the phone's light/dark setting, keep forest green as
the default, and let people pick their own colours. The app couldn't do any of them. Every
colour was read once, at import, into about 20 `StyleSheet.create` calls (~200 references
to `colors.x`), and `app.json` forced `userInterfaceStyle: "dark"`. A stylesheet built at
import can't change when the theme does.

There are two problems underneath:

1. **Plumbing.** Colours have to be read at render time.
2. **Legibility.** A custom theme is a way for someone to make the app unreadable.

## Options

**Plumbing**

| Option | Buys | Costs |
| --- | --- | --- |
| **Theme context + `makeStyles(palette => …)`, one stylesheet cached per palette** | No dependency; the same `StyleSheet` idiom the code already uses; custom palettes are just data | Touches every screen file (mechanical); every themed component re-renders on a theme change |
| Unistyles 3 | Near drop-in; theme changes skip React re-renders; adaptive themes built in | A native dependency with a Babel plugin and a C++ core, and magic to explain in review |
| NativeWind (Tailwind classes + CSS variables) | Industry-familiar | Rewrites every style as class names; the wrong idiom for this codebase |
| `PlatformColor` / `DynamicColorIOS` | Native light/dark with no re-render | Can't express a palette a person picked |

**Legibility**

| Option | Buys | Costs |
| --- | --- | --- |
| Free colour pickers for each token | Total freedom | Unreadable combinations are one drag away; twelve tokens to pick |
| **Two hues (base and accent), with every token derived in OKLCH and each one checked against WCAG contrast** | Any choice stays readable; two decisions instead of twelve | Less freedom; you can't pick an exact hex |
| Presets only | Nothing can go wrong | Not what was asked for |

## Decision

**A `ThemeProvider` holds `{ mode: system | light | dark, theme, hue, accent }`. Palettes
are plain data (`src/theme/palette.ts`). Screens read colours through `useTheme()` and
`makeStyles`.**

- **Forest dark keeps the exact hexes memo shipped with.** Nobody's app changes until they
  change it.
- **Forest light is designed by hand:** cream paper, deep-green ink, and an amber darkened
  to `#B06A12`. The original `#E2A04A` on cream is about 2:1, which is a record button you
  can't find.
- **Every other theme is derived from a base hue and an accent hue** at fixed OKLCH
  lightness steps. OKLCH is used because equal steps there look equal, which HSL's don't.
  Tide, Clay, Plum and Ink are presets; Custom is two sliders.
- **A guard checks each derived colour and moves its lightness until it passes:** text at
  least 4.5:1 and UI marks at least 3:1. The accent is checked against `surface`, the
  lower-contrast of the two backgrounds it sits on. A script checked every combination of
  base and accent hue in 10° steps, in both schemes: all pass.
- **An explicit Light or Dark is set with `Appearance.setColorScheme`,** so what iOS draws
  for memo follows the choice without each piece being told: action sheets, alerts, the
  keyboard, date pickers. System hands the choice back to iOS (`"unspecified"`).
  `app.json` is now `automatic`.
- **Preferences live on the device, in SecureStore.** Appearance is a per-device choice
  (iOS treats it that way), and it has to be known before the first frame, without a
  network round trip.

## What we're accepting

- **A theme change re-renders everything themed.** It's rare, and one stylesheet is built
  per palette and cached, but it isn't free. That's why the custom sliders preview live and
  only apply when you let go.
- **The custom theme doesn't follow you to another phone.** The account knows nothing
  about it.
- **Widgets keep the Forest colours.** They run in their own process, and sharing a
  custom palette would mean writing it to the app group on every change. Parked.
- **You can't pick an exact brand colour.** The guard would move it anyway.
- **This deviates from `docs/brand.md`'s single palette.** Brand now documents Forest
  as the default and the rules every derived theme keeps (cream, never white; one accent).

## Revisit when

- A second device, or a web client, needs the same theme: move the preference to `/v1/me`.
- Someone wants their theme on the widgets: write the palette to the app group.

## Interview angle

> **"How would you add dark mode and user themes to an existing app?"**

First, find where colours are bound. If they're baked into styles when the module loads,
nothing can change at runtime, so colours have to be read at render time: a context, plus
a style factory memoised per palette. Second, don't give users raw colour pickers. Derive
the palette from one or two seed hues in a perceptual space (OKLCH) and enforce WCAG
contrast in code. This is what Material You does. A custom theme should be unable to make
the app unreadable. Third, tell the OS about an explicit choice (`overrideUserInterfaceStyle`
on iOS), so system UI such as sheets and keyboards matches instead of each component being
patched separately.
