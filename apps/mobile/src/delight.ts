import { devicePref } from "./prefs";

// Small rewards for using memo (ADR 0041): milestones, memo's birthday, the season in
// the particles, a greeting for late nights. Each one is rare by design. A surprise that
// happens every day stops being one.

// --- seasons ------------------------------------------------------------------------

export type Particle = "fireflies" | "leaves" | "snow" | "petals";

// Timezones south of the equator, where September is spring. A timezone is the best
// hint of place memo has without asking for location, and it's right for everywhere
// memo is likely to be used; a miss only swaps which particles fall.
const SOUTH = [
  "Australia/",
  "Pacific/Auckland",
  "Pacific/Chatham",
  "Pacific/Fiji",
  "America/Argentina",
  "America/Sao_Paulo",
  "America/Santiago",
  "America/Montevideo",
  "America/Asuncion",
  "America/Lima",
  "America/La_Paz",
  "Africa/Johannesburg",
  "Africa/Maputo",
  "Africa/Harare",
  "Africa/Windhoek",
  "Indian/Mauritius",
  "Indian/Reunion",
  "Antarctica/",
];

function timeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

// Summer's fireflies, autumn's leaves, winter's snow, spring's petals.
export function seasonParticle(now = new Date(), zone = timeZone()): Particle {
  const south = SOUTH.some((prefix) => zone.startsWith(prefix));
  const month = (now.getMonth() + (south ? 6 : 0)) % 12; // 0 = January, northern terms
  if (month >= 2 && month <= 4) return "petals";
  if (month >= 5 && month <= 7) return "fireflies";
  if (month >= 8 && month <= 10) return "leaves";
  return "snow";
}

// Typing one of these into search sends it across the screen.
export function particleForSearch(query: string): Particle | null {
  const q = query.trim().toLowerCase();
  if (q === "fireflies" || q === "firefly") return "fireflies";
  if (q === "snow" || q === "let it snow") return "snow";
  if (q === "leaves" || q === "autumn" || q === "fall") return "leaves";
  if (q === "petals" || q === "blossom" || q === "spring") return "petals";
  return null;
}

// --- milestones ---------------------------------------------------------------------

export const MILESTONES = [100, 500, 1000, 2500, 5000, 10000];

const milestonePref = devicePref<{ last: number | null }>("memo.milestones", { last: null });

// The milestone to celebrate now, or null. The first time memo sees a count (this build
// arriving on a phone with 300 notes), it records where things stand without a party:
// only a crossing seen as it happens is celebrated, once, even if notes are deleted and
// the count crosses it again.
export async function milestoneReached(count: number): Promise<number | null> {
  const { last } = await milestonePref.get();
  const passed = MILESTONES.filter((m) => m <= count);
  const highest = passed.length ? passed[passed.length - 1] : 0;
  if (last === null) {
    await milestonePref.set({ last: highest });
    return null;
  }
  if (highest > last) {
    await milestonePref.set({ last: highest });
    return highest;
  }
  return null;
}

// --- memo's birthday ----------------------------------------------------------------

const birthdayPref = devicePref<{ shown: string | null }>("memo.birthday", { shown: null });

function localDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

// Whole years since the account was made, on the day it was made; otherwise 0. An
// account made on 29 February has its birthday on 28 February in other years.
export function yearsOfMemo(createdAt: string, now = new Date()): number {
  const made = new Date(createdAt);
  if (Number.isNaN(made.getTime())) return 0;
  const years = now.getFullYear() - made.getFullYear();
  if (years < 1) return 0;
  const leapBaby = made.getMonth() === 1 && made.getDate() === 29;
  const isLeap = (y: number) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
  const day = leapBaby && !isLeap(now.getFullYear()) ? 28 : made.getDate();
  return now.getMonth() === made.getMonth() && now.getDate() === day ? years : 0;
}

// The birthday to celebrate today, once per day at most.
export async function birthdayToday(createdAt: string, now = new Date()): Promise<number | null> {
  const years = yearsOfMemo(createdAt, now);
  if (!years) return null;
  const today = localDate(now);
  const { shown } = await birthdayPref.get();
  if (shown === today) return null;
  await birthdayPref.set({ shown: today });
  return years;
}

// --- the moment on Home -------------------------------------------------------------

export type Moment = { kind: "milestone"; count: number } | { kind: "birthday"; years: number };

export function momentText(m: Moment): { title: string; body: string } {
  if (m.kind === "milestone") {
    return {
      title: `Your ${m.count.toLocaleString()}th note.`,
      body: m.count >= 1000 ? "A thousand things you didn't have to hold in your head." : "memo remembers every one.",
    };
  }
  return {
    title: m.years === 1 ? "A year of memo." : `${m.years} years of memo.`,
    body: "Thank you for telling it things.",
  };
}

// "Good evening, Akin" - and between 1 and 5 in the morning, "Still up, Akin?".
export function greeting(name: string | null, now = new Date()): string {
  const hour = now.getHours();
  const first = name?.trim().split(/\s+/)[0];
  const day = now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  if (hour >= 1 && hour < 5) return `Still up${first ? `, ${first}` : ""}? · ${day}`;
  const part = hour < 5 ? "evening" : hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  return `Good ${part}${first ? `, ${first}` : ""} · ${day}`;
}
