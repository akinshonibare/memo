import type { Capture } from "./api";

export function formatStamp(iso: string, long = false): string {
  const d = new Date(iso);
  const date = d.toLocaleDateString(undefined, {
    month: long ? "long" : "short",
    day: "numeric",
    year: "numeric",
  });
  const time = d.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
  return `${date} · ${time}`;
}

// An event's start, in the device's local time: "Tue, Oct 6 · 12:00".
export function formatWhen(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
  const time = d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
  return `${date} · ${time}`;
}

// A todo's due date: "Fri, Oct 2".
export function formatDue(isoDate: string): string {
  const d = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
}

// A time of day on its own: "12:00".
export function formatTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", hour12: false });
}

// A calendar date with no time attached: "Monday, September 28".
export function formatDay(isoDate: string, withYear = false): string {
  const d = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString(undefined, {
    weekday: withYear ? undefined : "long",
    month: "long",
    day: "numeric",
    year: withYear ? "numeric" : undefined,
  });
}

// How long ago a calendar date was, in words: "today", "yesterday", "5 days ago",
// "3 weeks ago", "2 months ago", "over a year ago".
export function formatAgo(isoDate: string): string {
  const then = new Date(`${isoDate}T00:00:00`);
  if (Number.isNaN(then.getTime())) return isoDate;
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const days = Math.round((today.getTime() - then.getTime()) / 86_400_000);
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  if (days < 14) return `${days} days ago`;
  if (days < 60) return `${Math.round(days / 7)} weeks ago`;
  if (days < 365) return `${Math.round(days / 30)} months ago`;
  return "over a year ago";
}

// A reach-out rhythm in words: 30 -> "every month".
export function formatRhythm(days: number | null): string {
  if (days == null) return "never";
  const named: Record<number, string> = {
    7: "every week",
    14: "every 2 weeks",
    30: "every month",
    60: "every 2 months",
    90: "every 3 months",
    180: "every 6 months",
  };
  return named[days] ?? `every ${days} days`;
}

// What kind of contact a mention records, as a short word for a timeline.
export function contactWord(contact: string): string {
  switch (contact) {
    case "in_person":
      return "Saw them";
    case "call":
      return "Called";
    case "message":
      return "Messaged";
    case "unspecified":
      return "In touch";
    default:
      return "Mentioned";
  }
}

const pad = (n: number) => String(Math.abs(n)).padStart(2, "0");

// A calendar date for the API, from the device's local day: "2026-10-06".
export function toIsoDate(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

// An instant for the API with the device's offset kept, as extraction writes them:
// "2026-10-06T12:00:00-04:00", not the UTC "…T16:00:00.000Z" toISOString gives.
export function toLocalIso(d: Date): string {
  const offset = -d.getTimezoneOffset();
  const sign = offset >= 0 ? "+" : "-";
  const time = `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
  return `${toIsoDate(d)}T${time}${sign}${pad(Math.trunc(offset / 60))}:${pad(offset % 60)}`;
}

export function formatClock(ms: number): string {
  const total = Math.floor(ms / 1000);
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

const SHORT = 90;

// A short note is its own headline. A longer one is split into a headline (the first
// sentence or line) and the rest; voice notes without a transcript yet get a neutral
// title rather than an empty one.
export function headline(c: Pick<Capture, "kind" | "text">): { title: string; rest: string | null } {
  // A photo's text is its description, stored as "Photo: …"; the row shows a photo
  // marker instead, so the prefix would only repeat it.
  const raw = (c.text ?? "").trim();
  const text = c.kind === "image" ? raw.replace(/^Photo:\s*/, "") : raw;
  if (!text) {
    return { title: c.kind === "audio" ? "Voice note" : c.kind === "image" ? "Photo" : "Untitled", rest: null };
  }
  if (text.length <= SHORT) return { title: text, rest: null };
  const first = text.split(/\n|(?<=[.!?])\s/)[0] ?? text;
  if (first.length <= 64 && first.length < text.length) {
    return { title: first, rest: text.slice(first.length).trim() || null };
  }
  return { title: `${text.slice(0, 61).trimEnd()}…`, rest: text };
}

export function titleOf(c: Pick<Capture, "kind" | "text">): string {
  return headline(c).title;
}

export function statusLabel(status: Capture["status"]): string | null {
  switch (status) {
    case "pending":
      return "Queued";
    case "transcribing":
      return "Transcribing…";
    case "describing":
      return "Reading the photo…";
    case "extracting":
      return "Extracting…";
    case "embedding":
      return "Indexing…";
    case "failed":
      return "Couldn't process";
    default:
      return null;
  }
}

export const ACTIVE_STATUSES = new Set<Capture["status"]>([
  "pending",
  "transcribing",
  "describing",
  "extracting",
  "embedding",
]);

// How far back "on this day" reaches, in words: a note (ADR 0040) or a prayer (ADR 0042).
export function lookBack(on: string, today: string): string {
  const a = new Date(`${on}T00:00:00`);
  const b = new Date(`${today}T00:00:00`);
  const months = (b.getFullYear() - a.getFullYear()) * 12 + (b.getMonth() - a.getMonth());
  if (months >= 12 && months % 12 === 0) return months === 12 ? "A year ago today" : `${months / 12} years ago today`;
  if (months === 6) return "Six months ago today";
  if (months === 1) return "A month ago today";
  return `From ${formatDay(on, true)}`;
}
