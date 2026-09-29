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

## Push notifications: the APNs key (ADR 0047, once)

The morning push is sent through Expo's push service, which needs an Apple Push
Notifications key on the EAS project. Until it has one, every send fails and the push
row says `InvalidCredentials`. It needs an Apple login, so it runs in Akin's terminal,
from `apps/mobile`:

```bash
npx eas-cli@latest credentials --platform ios
```

1. **Which build profile?** → `production`.
2. **Do you want to log in to your Apple account?** → `Y`. Apple ID, password, then the
   six-digit 2FA code on the phone. (Only for this step; routine builds answer `N`.)
3. In the menu, choose the **Push Notifications** entry (managing the push key).
4. Choose to set up a push key, and when asked, **Generate a new Apple Push
   Notifications service key** (Expo's docs quote this wording). EAS creates the key
   with Apple and keeps it; nothing is downloaded.
   - Apple allows two push keys per team. If it says the limit is reached, reuse a key
     EAS already has, or revoke an unused one at developer.apple.com → Keys and
     generate again.
5. The credentials summary now lists a push key for `com.akinshonibare.memo`. It covers
   TestFlight and development builds, and nothing is needed on the server.

Why it isn't there already: EAS offers this during an *interactive* `eas build`, and
build 17 ran with `--non-interactive`. (The labels in steps 3-4 are from Expo's docs
and memory of the CLI; if they read differently, the push-key entry is the one.)

No build is needed: build 17 already has the push entitlement. After the update that
carries PR 2 is on the phone, turn on Settings → Morning → Morning summary; the phone
registers, and the next morning with something on sends.

Checking it worked, the morning after: `GET /v1/pushes` lists the push with `status:
"sent"`; a failed one has the reason (`InvalidCredentials` means step 4 didn't take).

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
