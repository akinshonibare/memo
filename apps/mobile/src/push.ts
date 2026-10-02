import * as Notifications from "expo-notifications";
import { useEffect, useRef } from "react";
import { Platform } from "react-native";
import type { Api } from "./api";

// memo's remote pushes: the morning summary (ADR 0047) and the week's summary on
// Sunday (ADR 0070). The server decides what to say and when (your chosen time, only
// when there's something to say); the phone only registers where to send it, and says
// what happened: opened, or 👍 / 👎 from the notification's buttons.
//
// JavaScript only, on purpose: build 17 already carries the push entitlement (the
// expo-notifications plugin always adds it), so this ships as an over-the-air update.

// The EAS project the push token belongs to (app.json → extra.eas.projectId). Written
// out rather than read through expo-constants, which isn't a dependency of the app.
const EAS_PROJECT_ID = "fb5218ca-c4c4-4ee2-b97d-0c38e4a7d58d";

// Each kind of push has a category of its own, so a tap can open the right place.
export type PushKind = "morning" | "week";
export const PUSH_KINDS: PushKind[] = ["morning", "week"];
export const DEFAULT_MORNING = "07:30";
export const DEFAULT_WEEKLY = "18:00";

// Both buttons open memo: iOS only hands an action to JavaScript without opening the
// app if the app happens to be running, so a background vote would usually be lost.
export async function registerPushCategories(): Promise<void> {
  if (Platform.OS !== "ios") return;
  for (const kind of PUSH_KINDS) {
    await Notifications.setNotificationCategoryAsync(kind, [
      { identifier: "up", buttonTitle: "👍 Useful", options: { opensAppToForeground: true } },
      { identifier: "down", buttonTitle: "👎 Not useful", options: { opensAppToForeground: true } },
    ]).catch(() => undefined);
  }
}

// Asking Expo for a token goes through Apple's push service first, and nothing times
// it out: on a bad connection (or a simulator) it can wait forever, and so would the
// switch. So it gets fifteen seconds.
const TOKEN_TIMEOUT_MS = 15_000;

function tokenWithin(ms: number): Promise<string> {
  return Promise.race([
    Notifications.getExpoPushTokenAsync({ projectId: EAS_PROJECT_ID }).then((t) => t.data),
    new Promise<string>((_, reject) => setTimeout(() => reject(new Error("timed out")), ms)),
  ]);
}

const NOT_ALLOWED =
  "memo can't send notifications. You can allow them in the iPhone's Settings → memo → Notifications.";
const UNREACHABLE = "memo couldn't reach Apple's notification service. Try again in a minute.";

// Turn a push on at `time` ("07:30"), or off with null. On: this phone's token goes to
// the server first, so the first one isn't lost to a missing phone.
export async function setPush(api: Api, kind: PushKind, time: string | null): Promise<string | null> {
  if (time !== null) {
    const current = await Notifications.getPermissionsAsync();
    const granted = current.granted || (await Notifications.requestPermissionsAsync()).granted;
    if (!granted) return NOT_ALLOWED;
    const token = await tokenWithin(TOKEN_TIMEOUT_MS).catch(() => null);
    if (!token) return UNREACHABLE;
    await registerPushCategories();
    await api.registerPushToken(token);
  }
  await api.updateProfile(kind === "morning" ? { morning_push_at: time } : { weekly_push_at: time });
  return null;
}

// On launch, while any push is on: register again. A token can change, and a new
// sign-in is a new session, which the server sends through.
export async function refreshPushToken(api: Api, on: boolean): Promise<void> {
  if (!on || Platform.OS !== "ios") return;
  try {
    if (!(await Notifications.getPermissionsAsync()).granted) return;
    await registerPushCategories();
    await api.registerPushToken(await tokenWithin(TOKEN_TIMEOUT_MS));
  } catch {
    // Offline, or Expo unreachable: the next launch tries again.
  }
}

// Before signing out, while the session still works. The server also stops sending
// the moment the session is revoked, so a failure here loses nothing.
export async function forgetPushToken(api: Api): Promise<void> {
  if (Platform.OS !== "ios") return;
  try {
    if (!(await Notifications.getPermissionsAsync()).granted) return;
    // Short: signing out shouldn't wait on Apple. The revoked session is the real stop.
    await api.forgetPushToken(await tokenWithin(3_000));
  } catch {
    // The revoked session stops pushes anyway.
  }
}

function isPushKind(value: unknown): value is PushKind {
  return value === "morning" || value === "week";
}

// A tapped push opens its place (Today, or the weekly review) and counts as opened;
// a button is a vote.
export function usePushTaps(ready: boolean, api: Api | null, open: (kind: PushKind) => void) {
  const response = Notifications.useLastNotificationResponse();
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!ready || !api || !response) return;
    const data = response.notification.request.content.data ?? {};
    if (!isPushKind(data.kind) || typeof data.push_id !== "string") return;
    const key = `${response.notification.request.identifier}:${response.actionIdentifier}`;
    if (handled.current === key) return;
    handled.current = key;
    const pushId = data.push_id;
    const vote = response.actionIdentifier;
    const told =
      vote === "up" || vote === "down" ? api.pushFeedback(pushId, vote) : api.pushOpened(pushId);
    void told.catch(() => undefined);
    open(data.kind);
    Notifications.clearLastNotificationResponse();
  }, [ready, api, response, open]);
}
