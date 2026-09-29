# Shipping the app to a phone with EAS

The Expo client lives in `apps/mobile`. It is built in the cloud by EAS and installed on
registered iPhones as an ad-hoc build; nothing goes through TestFlight or the App Store
yet.

## The pieces

| Thing | Value | Where it lives |
| --- | --- | --- |
| Expo account | `akiin` | `npx eas-cli whoami` |
| EAS project | `@akiin/memo`, id `fb5218ca-c4c4-4ee2-b97d-0c38e4a7d58d` | `app.json` → `extra.eas.projectId` |
| Bundle identifier | `com.akinshonibare.memo` | `app.json` → `ios.bundleIdentifier` |
| Apple team | Akinkunmi Shonibare (Individual), `JS6AT5C3S2` | Apple developer account |
| Capabilities | Sign In with Apple (`ios.usesAppleSignIn`) | synced to the App ID by EAS at build time |
| Build profiles | `preview` (internal distribution, installable), `development` (dev client), `production` | `eas.json` |
| Credentials | distribution certificate + ad-hoc provisioning profile, stored on EAS | `eas credentials` |

Sign in with Apple does not work in Expo Go; it needs one of these builds.

## Build and install

```bash
cd apps/mobile
npx eas-cli@latest build --platform ios --profile preview
```

Prompts you will see, in order:

1. **"Do you want to log in to your Apple account?"** — `N` once the certificate and
   profile exist (they do). `Y` only when a capability or the device list changed; that
   needs your Apple ID, password and a 2FA code.
2. The build uploads, runs for ten to fifteen minutes, and prints an install link plus a
   QR code. Open the link on the iPhone and tap Install.

`autoIncrement` bumps the build number on every `preview` build; the marketing version is
`app.json` → `version`.

## Registering a new iPhone

Ad-hoc builds only install on devices whose UDID is in the provisioning profile.

```bash
npx eas-cli@latest device:create
```

Choose **Website**, open the link on the phone, install the profile it offers. Then the
next build with `Y` at the Apple prompt regenerates the provisioning profile to include it.
The phone also needs **Settings → Privacy & Security → Developer Mode** on.

Registered so far: one iPhone, UDID `00008130-001E59203AB8001C`.

## Logging in to EAS from a terminal that cannot open a browser

`eas login` prints a link and waits. If the browser does not open, open the link yourself
in the browser where you are signed in to expo.dev:

```bash
open "<the printed link>"
```

The confirmation page completes the login; the terminal prints `Logged in`.

## What the app needs from the server

Nothing to paste. The API base URL defaults to production in `src/config.ts`
(`EXPO_PUBLIC_API_URL` overrides it at build time). Sign in with Apple issues the session
(ADR 0019). Settings holds a server override for pointing a build at a local API.

## When to move to TestFlight

Ad-hoc profiles expire after a year and cap at 100 devices. Neither matters for one
phone. TestFlight (`eas build --profile production` then `eas submit`) is the next step
only when someone who is not you needs the app.

## Over-the-air updates (ADR 0037)

Builds from this change on include `expo-updates`, with a runtime version that is a
fingerprint of the native project. JavaScript-only changes (screens, colours, motion)
can then ship without a build:

```bash
cd apps/mobile
npx eas-cli@latest update --channel preview --message "what changed"
```

- The phone downloads it on the next launch and applies it on the launch after that.
- It only reaches builds whose native fingerprint matches. Adding a native module, a
  config plugin or an `app.json` change that touches native code needs a new build.
  `npx @expo/fingerprint fingerprint:generate --platform ios` prints the current one
  (its `hash`) if in doubt.
- Build 14 and earlier don't contain `expo-updates` and never get updates.
- `expo-notifications` adds the push entitlement (`aps-environment`). EAS syncs the
  capability to the App ID on the next build; answer `Y` at the Apple prompt if it asks.

## Running a development build in the simulator

For checking changes before any EAS build:

```bash
cd apps/mobile
npx expo run:ios --device "iPhone 17"
```

- Sign in with Apple needs an Apple ID the simulator doesn't have. Against a local API,
  a development build can be handed a session minted there: set `EXPO_PUBLIC_API_URL`
  and `EXPO_PUBLIC_DEV_SESSION` when starting Metro. Only `__DEV__` builds read the
  session variable (`src/config.ts`).
- **Xcode 26.2 can't compile `expo-modules-jsi` 57.1.1** (it builds from source on every
  machine): a C++ `SWIFT_RETURNS_RETAINED` annotation and Swift 6 "sending … risks
  causing data races" errors. EAS's Xcode builds it fine; build 14 came from the same
  version. Locally, either use EAS, or wait for an Expo patch release. The errors are in
  Expo's code, not memo's. A local-only patch to `node_modules/expo-modules-jsi` gets a
  simulator build through, but **run `npm ci` before any EAS build**: the patch changes
  the native fingerprint computed on the Mac, EAS computes it again from a clean install,
  and the mismatch fails the build at "Configure expo-updates" ("Runtime version
  mismatch"). That cost build 16.
