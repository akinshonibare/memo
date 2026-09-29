# 0038 — A recording keeps going with the phone locked, shown as a Live Activity

**Status:** active
**Date:** 2026-09-29

## Problem

Voice notes are the capture memo is built around, and they're often long. Until now, locking the phone
or switching apps paused the recorder: `expo-audio` pauses every recorder when the app
leaves the foreground unless told otherwise. Nothing on the Lock Screen said a note was
still open. Apple's Voice Memos records with the screen off; memo should too.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Keep pausing in the background | Nothing to build | A note cut short whenever the screen locks; an invisible paused state |
| Background recording with no Lock Screen presence | Recording continues | A live microphone with only iOS's orange dot to show for it, and no way back into the note from the Lock Screen |
| **Background recording plus a Live Activity** (`expo-widgets`, which memo already uses for widgets) | The Lock Screen and Dynamic Island show it's recording, with a clock iOS keeps ticking itself; tapping it returns to memo | One more extension layout to maintain; iOS 16.2+; a permission prompt the first time |

## Decision

**`allowsBackgroundRecording: true` while the record sheet is open
(`enableBackgroundRecording` in the `expo-audio` plugin), and a `RecordingActivity` Live
Activity for as long as it records.**

- **The clock is SwiftUI's own timer text** (`Text(date, style: .timer)`), built from a
  start time. There's no update per second. Pausing sends one update showing the
  still time. Resuming moves the start time on by the paused interval, so the clock shows
  recorded time.
- **Tapping it opens `memo://record`:** the existing link flow, and the sheet is already
  open.
- **It ends however the sheet ends:** End, discard, or the component unmounting. On start,
  any activity left over from a recording the app was killed during is ended first.
- **A failure to start the activity never stops the recording** (Live Activities turned
  off in Settings, an older iOS).
- **The layout follows the Lock Screen's appearance.** The Dynamic Island is always dark.

## What we're accepting

- **The activity shows Forest colours,** not a custom theme. Same reason as widgets
  (ADR 0031).
- **There's no End button in the Live Activity.** Interactive Live Activities need an App
  Intent wired to the recorder; tapping through to the sheet is one more step.
- **The microphone stays live while the phone is locked.** That's what was asked for, and
  iOS shows its own indicator.

## Revisit when

- People want to end a note from the Lock Screen: add a `LiveActivityIntent`.
- The Siri "Record in memo" intent (ADR 0028) should start a recording without opening
  the app.

## Interview angle

> **"How do you keep a long-running task visible when the user leaves your app?"**

Keep the work running with the right background mode, here audio recording, and give it
a surface outside the app: on iOS, a Live Activity. Let the system render anything that
changes every second, such as a timer text from a start date, rather than pushing updates:
updates are budgeted and throttled. Make the presence surface best-effort, so a user who
turned Live Activities off still gets their recording. And tie its lifetime to the task's
lifetime in every exit path, including the app being killed.
