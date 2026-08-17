# 0007 — Expo managed workflow, not native Swift

**Status:** accepted for V1
**Date:** 2026-08-17

## Problem

V1 needs three screens on a phone: capture (text + hold-to-record), extraction review, and
ask (with streamed answers). The roadmap eventually wants Action Button capture (V2),
EventKit calendar reads (V2), and WebSocket streaming audio with VAD (V1.5).

## Options

| Option | What it buys | What it costs |
| --- | --- | --- |
| **Native Swift / SwiftUI** | The only way to get App Intents / Action Button, EventKit, background audio, and Live Activities properly. Best possible capture latency, which for a capture app is the whole product. Nothing is off-limits. | Akin's learning budget this project is spent on Python. Slowest path to three working screens. Every backend change needs a rebuild-and-install cycle. Learning SwiftUI *and* async Python *and* RAG at once means learning none of them. |
| **Expo, managed workflow** | Fastest path to three screens. JS reload instead of rebuild — the iteration loop is seconds, which matters when the app is a thin client you'll change constantly while the backend churns. OTA updates. `expo-av` handles record-and-upload; SSE streaming works over `fetch`. | The managed workflow forbids custom native modules. Anything needing native code — a custom audio streaming module, App Intents — is blocked until you eject. Capture latency is worse than native, and for a capture app that's a real product cost. |
| **Expo + dev client (prebuild)** | Everything managed gives you, plus custom native modules. | Now you're managing native project files, CocoaPods, and build config. Loses the "just works" property that made Expo attractive. |
| **Bare React Native** | Full native access from day one. | All of Expo's costs with none of its conveniences. Little reason to start here in 2026. |

## Decision

Expo managed for V1, because the phone app is a thin client over an API that will change
weekly, and a seconds-long iteration loop beats native capability we don't need until V2.

## What we're accepting

We accept worse capture latency than native — the one place the product is most sensitive
to it. We accept that Action Button capture, the single best feature on the roadmap for a
capture app, is unavailable until we leave the managed workflow.

The important nuance: **this is not a one-way door.** `expo prebuild` converts a managed
project to a dev-client project in place; it is not a rewrite. So the correct framing is
"we're deferring native access at the cost of a future prebuild step", not "we're locked
out of native".

Keep the exit cheap: no Expo-managed-only APIs in business logic, and all server
communication behind a thin client module so a future native rewrite has one seam.

## Revisit when

- **V1.5**: if streaming audio over WebSocket with VAD needs a custom native module,
  `expo prebuild` to a dev client. Check `expo-av` / `expo-audio` capabilities *before*
  designing the protocol, not after.
- **V2**: App Intents / Action Button forces the dev client regardless.
- If measured capture latency (tap → recording started) exceeds ~300ms and it's Expo's
  fault rather than ours.

## Interview angle

> **"React Native or native?"**

The real question is which constraints are permanent and which are temporary. Cross-platform
frameworks lose on: deep OS integration (widgets, intents, background execution), raw
latency, and platform-idiomatic feel. They win on: iteration speed, one codebase, and OTA
updates. So the decision is "does this app's value live in OS integration?"

For a *capture* app, the honest answer is that it eventually does — the Action Button is
the killer feature — which is why this ADR is scoped to V1 and has an explicit exit rather
than pretending Expo is permanently correct. Naming the escape hatch (`expo prebuild`, in
place, not a rewrite) and its cost is what turns this from a preference into a plan.
