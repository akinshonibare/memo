# 0037 — Over-the-air updates: EAS Update, with a runtime version from the native fingerprint

**Status:** active
**Date:** 2026-09-29

## Problem

Every change to the app, even a colour or an animation's timing, meant an EAS build, a
TestFlight upload and a reinstall. Two builds were rejected this week (ITMS-90626) and had
to be rebuilt. Most of what memo's app does is JavaScript, which doesn't need a new binary.

The real hazard with over-the-air updates is compatibility. JavaScript that expects a
native module the installed binary doesn't have crashes at launch.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| No OTA; a build for everything | Nothing to get wrong | A slow loop for small fixes |
| **EAS Update, `runtimeVersion: { policy: "fingerprint" }`** | The runtime version is a hash of the native project. An update is only offered to binaries whose native code matches it, computed rather than remembered. | A new build whenever native code changes, which is correct but means more builds than a hand-bumped version would |
| EAS Update, `policy: "appVersion"` (what `eas update:configure` writes) | Fewer builds | memo's `version` stays `1.0.0` (EAS increments only the build number), so every binary would share one runtime version, and an update needing a new native module would reach old binaries and crash them |

## Decision

**`expo-updates` with `updates.url` set to memo's EAS project, the fingerprint policy,
and a channel per build profile** (`development`, `preview`, `production`).

- **Default behaviour:** check on launch, download in the background, apply on the next
  cold start. A launch never waits on the network.
- **Publishing** is `npx eas-cli@latest update --channel preview --message "…"`, from Akin's
  terminal (see the runbook).
- **This change itself needs one new build,** because it adds native modules. After
  that, JavaScript-only changes can ship as updates.

## What we're accepting

- **The first update only reaches the build that contains `expo-updates`.** Build 14
  won't get it.
- **An update applies on the second launch,** not the first. A user who opens the app
  once sees the old version that time.
- **A second release channel to reason about:** "what's on the phone" is now the binary
  plus the latest compatible update.

## Revisit when

- An update needs to apply at once (a crash fix): use `checkAutomatically` and
  `Updates.reloadAsync()` for that one.
- Builds become rare enough that the fingerprint's extra builds cost nothing to notice.

## Interview angle

> **"How do you ship hotfixes to a React Native app safely?"**

Over-the-air JavaScript updates, keyed to a runtime version that describes the native
binary exactly. The failure mode is shipping JavaScript that calls native code the
installed binary doesn't have. A hand-maintained version number is one forgotten bump
away from that crash. A fingerprint of the native project makes compatibility computed,
not remembered. Apply updates on the next launch so startup never blocks on the network,
and keep a separate channel per build profile.
