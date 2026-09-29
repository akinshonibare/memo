import * as Notifications from "expo-notifications";
import { useEffect, useRef } from "react";
import { Platform } from "react-native";
import type { Derivation } from "./api";
import { formatTime } from "./format";
import { devicePref } from "./prefs";

// Event reminders (ADR 0036): a local notification a little before each event memo
// knows about. Not the daily push ADR 0023 decided against - no server, no token, one
// nudge per event you'd otherwise miss. Off until you turn it on.
//
// Scheduling is a sync, not a queue: every pass works out the notifications there
// should be and makes the phone match - adding missing ones, cancelling ones for events
// that moved, were rejected or passed. Running it twice changes nothing. It runs as
// part of the phone sync (src/sync.ts), which already has the items in hand.

export const remindersPref = devicePref("memo.eventReminders", { on: false, minutes: 15 });
export const LEAD_CHOICES = [5, 15, 30, 60];

const PREFIX = "memo-event:";
// iOS keeps at most 64 pending local notifications per app; leave room for others.
const MAX_SCHEDULED = 50;
const HORIZON_DAYS = 30;

if (Platform.OS === "ios") {
  // A reminder that arrives while memo is open still shows as a banner.
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

export async function enableReminders(on: boolean, minutes: number): Promise<string | null> {
  if (on) {
    const current = await Notifications.getPermissionsAsync();
    const granted = current.granted || (await Notifications.requestPermissionsAsync()).granted;
    if (!granted) {
      return "memo can't send notifications. You can allow them in the iPhone's Settings → memo → Notifications.";
    }
  }
  await remindersPref.set({ on, minutes });
  return null;
}

type Wanted = { identifier: string; at: Date; title: string; body: string; data: Record<string, string> };

export function wantedReminders(events: Derivation[], minutes: number, now = new Date()): Wanted[] {
  const horizon = now.getTime() + HORIZON_DAYS * 86_400_000;
  const out: Wanted[] = [];
  for (const d of events) {
    if (d.kind !== "event" || d.status === "rejected") continue;
    const startsAt = d.payload.starts_at;
    if (typeof startsAt !== "string") continue;
    const start = new Date(startsAt);
    if (Number.isNaN(start.getTime())) continue;
    const at = new Date(start.getTime() - minutes * 60_000);
    if (at.getTime() <= now.getTime() + 5_000 || start.getTime() > horizon) continue;
    const where = typeof d.payload.location === "string" && d.payload.location ? ` · ${d.payload.location}` : "";
    out.push({
      // The start time is part of the identity: an event moved to another time is a
      // different notification, and the old one is cancelled.
      identifier: `${PREFIX}${d.id}:${start.getTime()}:${minutes}`,
      at,
      title: String(d.payload.title ?? "Event"),
      body: `${formatTime(startsAt)}${where}`,
      data: { captureId: d.capture_id ?? "", derivationId: d.id },
    });
  }
  return out.sort((a, b) => a.at.getTime() - b.at.getTime()).slice(0, MAX_SCHEDULED);
}

// Called by the phone sync (src/sync.ts) with the items it has already fetched.
export async function syncNotifications(items: Derivation[]): Promise<void> {
  if (Platform.OS !== "ios") return;
  try {
    const { on, minutes } = await remindersPref.get();
    const scheduled = (await Notifications.getAllScheduledNotificationsAsync()).filter((n) =>
      n.identifier.startsWith(PREFIX),
    );
    const wanted = on && (await Notifications.getPermissionsAsync()).granted ? wantedReminders(items, minutes) : [];
    const want = new Set(wanted.map((w) => w.identifier));
    const have = new Set(scheduled.map((n) => n.identifier));
    for (const n of scheduled) {
      if (!want.has(n.identifier)) await Notifications.cancelScheduledNotificationAsync(n.identifier);
    }
    for (const w of wanted) {
      if (have.has(w.identifier)) continue;
      await Notifications.scheduleNotificationAsync({
        identifier: w.identifier,
        content: { title: w.title, body: w.body, data: w.data },
        trigger: { type: Notifications.SchedulableTriggerInputTypes.DATE, date: w.at },
      });
    }
  } catch {
    // A failed pass leaves what was scheduled; the next pass tries again.
  }
}

// Signing out cancels every event notification memo scheduled (src/sync.ts clearPhone).
export async function cancelNotifications(): Promise<void> {
  if (Platform.OS !== "ios") return;
  const scheduled = await Notifications.getAllScheduledNotificationsAsync().catch(() => []);
  for (const n of scheduled) {
    if (n.identifier.startsWith(PREFIX)) await Notifications.cancelScheduledNotificationAsync(n.identifier).catch(() => {});
  }
}

// A tapped reminder opens the note its event came from (or the Planner for an event
// added there), once the app is signed in and unlocked.
export function useReminderTaps(ready: boolean, open: (captureId: string | null) => void) {
  const response = Notifications.useLastNotificationResponse();
  const handled = useRef<string | null>(null);
  useEffect(() => {
    if (!ready || !response) return;
    const id = response.notification.request.identifier;
    if (!id.startsWith(PREFIX) || handled.current === id) return;
    if (response.actionIdentifier !== Notifications.DEFAULT_ACTION_IDENTIFIER) return;
    handled.current = id;
    const captureId = response.notification.request.content.data?.captureId;
    open(typeof captureId === "string" && captureId ? captureId : null);
    Notifications.clearLastNotificationResponse();
  }, [ready, response, open]);
}
