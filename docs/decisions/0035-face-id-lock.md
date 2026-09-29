# 0035 — Face ID lock: a device setting, a cover over everything, a minute away

**Status:** active
**Date:** 2026-09-29

## Problem

memo holds everything a person says to it. Anyone holding an unlocked phone can open it,
and the app switcher shows its last screen to whoever picks the phone up.

## Options

| Option | Buys | Costs |
| --- | --- | --- |
| Rely on the phone's own lock | Nothing to build | An unlocked phone handed to someone is an open diary; the app switcher shows your last note |
| **A local Face ID lock (`expo-local-authentication`), off by default** | Your notes need your face (or passcode) even on an unlocked phone; the app switcher shows nothing | One more thing that can get in the way of quick capture; a native module |
| Encrypt data at rest with a biometric-bound key | Protects against a lost, unlocked device's storage | Notes live on the server; the phone holds almost nothing. Wrong layer. |

## Decision

**Settings → Privacy → Lock with Face ID. Off by default, stored on the device.**

- **When it asks:** on a cold start, and on returning after more than a minute away. The
  Face ID sheet itself makes the app briefly inactive; that doesn't count as leaving.
- **The app switcher:** while the lock is on, leaving the foreground puts a cover over
  memo.
- **Turning it on runs Face ID first,** so nobody enables a lock they can't open. If
  there's no Face ID and no passcode, it says so and stays off.
- **The lock is a cover drawn inside the app and inside every sheet** (`<LockCover />`,
  the last child of each). The first version presented it as its own `Modal`; review
  caught that iOS won't present a modal over one that's already showing, so over an open
  sheet the lock silently never appeared. Nothing underneath is unmounted, so a
  recording in progress keeps recording behind the lock, and a half-written note keeps
  its text.
- **Turning it off asks for Face ID too,** or anyone holding the unlocked app could
  remove it.
- **If the phone's passcode is removed** after the lock was turned on, Face ID can never
  succeed again. memo notices (nothing is enrolled at any level) and turns the lock off
  rather than locking its owner out for good.
- **Deep links, the share sheet and notification taps wait** until it's unlocked (the
  existing `pending` pattern), so a Siri note can't open the app past the lock.

## What we're accepting

- **Quick capture gets one Face ID glance** when the lock is on. Face ID takes under a
  second, and the lock is opt-in.
- **Widgets and the Lock Screen Live Activity still show what they show:** today's items,
  the recording timer. They're outside the app.
- **It's per device,** like iOS's own app locks.
- **One minute is fixed,** not a setting.

## Revisit when

- Someone wants it immediately, or after 5 minutes: add the choice.
- iOS's system-level app lock (hide and require Face ID for any app) becomes the norm:
  point to it and consider removing this.

## Interview angle

> **"How would you add a biometric lock to an app?"**

Put the lock above everything, not beside it, and check that "everything" includes
open sheets. On iOS a modal can't be presented over another modal, so a lock that is
itself a modal silently fails exactly when a sheet is open; draw the cover inside each
presented surface instead. Don't unmount what's underneath, or you lose in-progress
state such as a recording or a draft. Cover the app when it leaves the foreground, so the
switcher snapshot is blank. Hold incoming deep links until it's unlocked. And verify
biometrics work before letting someone turn the lock on, or off.
