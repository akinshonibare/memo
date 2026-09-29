import {
  isHealthDataAvailable,
  queryWorkoutSamples,
  requestAuthorization,
  WorkoutTypeIdentifier,
} from "@kingstinct/react-native-healthkit";
import { useEffect, useRef } from "react";
import { AppState, Platform } from "react-native";
import { type Api, ApiError, type WorkoutIn } from "./api";
import { devicePref } from "./prefs";

// Apple Health (ADR 0050): one switch in Settings, and workouts read, never written.
// Memo's one exception to only ever writing to the phone (ADR 0034): the workouts count
// habits' days and carry their distance and time into milestones. Read on this phone,
// sent to memo, which files each as an activity.
//
// Each pass sends a whole window - two years the first time, the last 30 days after -
// so it is a replacement, not a queue: the server files what's new and lets go of what
// Health no longer has. Running one twice does nothing the second time.

export const healthPref = devicePref("memo.health", { on: false, fullSyncAt: 0, lastSyncAt: 0 });

const DAY = 86_400_000;
const FIRST_WINDOW_DAYS = 730; // history, so a "first 10k" is a first
const WINDOW_DAYS = 30;
const EVERY_MS = 15 * 60_000; // at most one pass per quarter hour, on opening the app

// Read access only. Distances ride on the workout itself; asking for the distance
// types too keeps a workout's distance readable on every iOS version.
const READ = [
  WorkoutTypeIdentifier,
  "HKQuantityTypeIdentifierDistanceWalkingRunning",
  "HKQuantityTypeIdentifierDistanceCycling",
  "HKQuantityTypeIdentifierDistanceSwimming",
] as const;

export function healthAvailable(): boolean {
  if (Platform.OS !== "ios") return false;
  try {
    return isHealthDataAvailable();
  } catch {
    return false; // an older build without the module
  }
}

let running: Promise<boolean> | null = null;
let lastFiled = -1; // what the last pass filed, so an unchanged pass changes nothing

// One pass: read the window's workouts and send them. True when memo's count changed.
export function syncHealth(api: Api, { force = false } = {}): Promise<boolean> {
  running ??= (async () => {
    try {
      const pref = await healthPref.get();
      if (!pref.on || !healthAvailable()) return false;
      const now = Date.now();
      if (!force && now - pref.lastSyncAt < EVERY_MS) return false;
      const full = !pref.fullSyncAt;
      const since = new Date(now - (full ? FIRST_WINDOW_DAYS : WINDOW_DAYS) * DAY);
      const until = new Date(now);
      const found = await queryWorkoutSamples({
        limit: 0,
        ascending: true,
        filter: { date: { startDate: since, endDate: until, strictStartDate: true } },
      });
      const workouts: WorkoutIn[] = found
        .filter((w) => w.startDate >= since && w.startDate <= until)
        .map((w) => ({
          id: w.uuid,
          activity_type: w.workoutActivityType,
          start: w.startDate.toISOString(),
          end: w.endDate.toISOString(),
          duration_s: Math.max(0, w.duration.quantity),
          distance_m: w.totalDistance ? w.totalDistance.quantity : null,
        }));
      const out = await api.syncWorkouts(since.toISOString(), until.toISOString(), workouts);
      await healthPref.set({ on: true, lastSyncAt: now, fullSyncAt: full ? now : pref.fullSyncAt });
      const moved = out.removed > 0 || out.filed !== lastFiled;
      lastFiled = out.filed;
      return moved;
    } catch (e) {
      // Habits turned off on another phone (409), Health locked, or offline: next time.
      if (!(e instanceof ApiError && e.status === 409)) console.warn("health sync", e);
      return false;
    } finally {
      running = null;
    }
  })();
  return running;
}

// The Settings switch. On: ask iOS for read access, then read two years. Off: memo
// forgets every workout it read. Returns what to tell the user, or null.
export async function setHealthSync(api: Api, on: boolean): Promise<string | null> {
  if (!on) {
    await api.forgetWorkouts();
    await healthPref.set({ on: false, fullSyncAt: 0, lastSyncAt: 0 });
    return null;
  }
  if (!healthAvailable()) return "Apple Health isn't available on this device.";
  try {
    await requestAuthorization({ toRead: READ });
  } catch {
    return "memo couldn't ask Health for your workouts. Try again in a moment.";
  }
  // iOS never says whether reading was allowed - a refusal just reads as no workouts.
  await healthPref.set({ on: true, fullSyncAt: 0, lastSyncAt: 0 });
  await syncHealth(api, { force: true });
  return null;
}

// Signing out: this phone stops sending anyone's workouts until it's turned on again.
export function forgetHealthOnThisPhone(): Promise<void> {
  return healthPref.set({ on: false, fullSyncAt: 0, lastSyncAt: 0 });
}

// A pass on launch and whenever memo comes back to the front, like the phone sync.
export function useHealthSync(api: Api | null, onChanged: () => void) {
  const health = healthPref.use();
  const changed = useRef(onChanged);
  useEffect(() => {
    changed.current = onChanged;
  }, [onChanged]);
  useEffect(() => {
    if (!api || !health.on) return;
    const run = () => void syncHealth(api).then((moved) => moved && changed.current());
    run();
    const sub = AppState.addEventListener("change", (s) => s === "active" && run());
    return () => sub.remove();
  }, [api, health.on]);
}
