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
  const text = (c.text ?? "").trim();
  if (!text) return { title: c.kind === "audio" ? "Voice note" : "Untitled", rest: null };
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
  "extracting",
  "embedding",
]);
