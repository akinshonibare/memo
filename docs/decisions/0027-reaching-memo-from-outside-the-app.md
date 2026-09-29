# 0027 — Reaching memo from outside the app: links, a share extension, widgets

**Status:** active
**Date:** 2026-09-28

## Problem

Capture that needs the app open and a button found is slow capture, and the brief's
whole premise is that capture must be nearly free. Four ways in were asked for: Siri,
the Action Button, the share sheet, and widgets. Each needs something outside the app
process, which in an Expo managed app means config plugins and extra native targets,
and every extra target is a bundle ID, a provisioning profile and a place a build can
break.

## Options

| Need | Options | Chosen, and why |
| --- | --- | --- |
| Siri and the Action Button | **App Intents** (a native Swift target, real Siri phrases and Shortcuts actions) or **a URL scheme** that a one-action Shortcut opens | **URL scheme** (`memo://record`, `write`, `ask`, `planner`). The Action Button runs Shortcuts, and a named Shortcut is a Siri phrase, so this covers both with no native code. App Intents would add discoverability and parameters (for example "Siri, tell memo …" with the text), at the cost of Swift and a target |
| Share sheet | `expo-share-intent` (the extension hands content to the app through an App Group) or `expo-share-extension` (renders React Native UI *inside* the extension) | **`expo-share-intent`** 8.x: it targets SDK 57, was maintained this year, and memo already has the capture UI in the app, so the extension only needs to hand content over. Rendering UI in the extension would mean a second React Native runtime under the extension's memory limit |
| Widgets | `expo-widgets` (first-party, SDK 57: widget UI written in JS with Expo UI's SwiftUI components) or a hand-written WidgetKit target via `@bacons/apple-targets` | **`expo-widgets`**: first-party, the same language as the app, and `widgetURL` for tap-through. A hand-written Swift widget gives full WidgetKit control in exchange for a second language to maintain |

## Decision

- **Links:** `memo://record | write | ask | planner`. A link that arrives on a cold start
  waits until sign-in and onboarding are done. Profile explains the Shortcuts setup.
- **Share:** photos become photo captures at once, through the same resize and JPEG
  re-encode as the picker (HEIC from Photos included). Links and text open a new note
  prefilled, so the reason for keeping them can be added.
- **Widgets:** *Today* (small, medium, lock-screen rectangular) shows the morning view's
  events and todos and opens the Planner. *Record* (small, lock-screen circular) opens
  straight into recording.
- One App Group, `group.com.akinshonibare.memo`, shared by both extensions. EAS learns
  about both targets from the plugins (`extra.eas.build.experimental.ios.appExtensions`)
  and provisions them.

## What we're accepting

- **The Today widget shows what the app last saw.** It is pushed from the morning view
  when the app loads it, so a widget can be a day stale if the app isn't opened. A
  timeline or background refresh would fix it, at the cost of a background task.
- **Two more signed targets.** Every build now provisions three bundle IDs. The first
  build after this change needs an Apple login to create them.
- **Untested until it runs on a phone.** A local prebuild confirms the targets, App
  Group, entitlements and URL scheme are generated correctly. The extensions' behaviour
  can only be checked on a device.
- **No real Siri vocabulary.** "Hey Siri, memo note" works only because the user named
  a Shortcut that. memo doesn't teach Siri anything itself.

## Revisit when

- People want to dictate the note *to Siri* rather than open the recorder: that is App
  Intents with a text parameter, and the first native Swift target in the repo.
- The stale widget annoys: move to `updateTimeline` fed by a background fetch.

## Interview angle

> **"How do you get content into your app from outside it?"**

Count the native targets before choosing features. Each extension is a bundle ID,
provisioning, entitlements and a new way for a build to fail, so buy as much as possible
with the cheapest mechanism first. Here, a URL scheme plus the user's own Shortcut
covers Siri and the Action Button with zero native code. The share extension only hands
content to the app through an App Group rather than running UI itself, because the app
already has the capture flow and extensions have tight memory limits. And the widget
uses the first-party JS widget runtime rather than a second language. The honest costs:
the widget is a snapshot, not live; and Siri integration is borrowed from Shortcuts,
not native.
