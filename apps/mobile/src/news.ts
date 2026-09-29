import { useCallback, useEffect, useState } from "react";
import { AppState } from "react-native";
import type { Api, News } from "./api";
import { devicePref } from "./prefs";

// What's new in each of memo's places, for the Places button on Home: the server says
// when each last got something (GET /v1/news), this phone remembers when you last
// looked, and a place with something newer is marked. No counts: a mark, not a tally.

export type PlaceKey = keyof News;
const PLACES: PlaceKey[] = ["planner", "review", "people", "faith", "habits"];

// The screens that count as looking at a place.
export const ROUTE_PLACE: Record<string, PlaceKey> = {
  Planner: "planner",
  Ideas: "planner",
  Review: "review",
  People: "people",
  Person: "people",
  Faith: "faith",
  Habits: "habits",
  Habit: "habits",
};

const seenPref = devicePref<Partial<Record<PlaceKey, number>>>("memo.placesSeen", {});

export async function markSeen(place: PlaceKey) {
  const seen = await seenPref.get();
  await seenPref.set({ ...seen, [place]: Date.now() });
}

// The places with something you haven't seen. The first time, everything counts as
// seen: an old memo shouldn't light up all at once.
export function useNews(api: Api, refreshKey: number): Set<PlaceKey> {
  const seen = seenPref.use();
  const [news, setNews] = useState<News | null>(null);

  const load = useCallback(async () => {
    try {
      const now = Date.now();
      const stored = await seenPref.get();
      if (PLACES.every((p) => stored[p] === undefined)) {
        await seenPref.set(Object.fromEntries(PLACES.map((p) => [p, now])));
      }
      setNews(await api.getNews());
    } catch {
      // A mark is a nicety: without the answer, nothing is marked.
    }
  }, [api]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => s === "active" && void load());
    return () => sub.remove();
  }, [load]);

  const fresh = new Set<PlaceKey>();
  if (news) {
    for (const p of PLACES) {
      const newest = news[p] ? Date.parse(news[p]!) : NaN;
      const looked = seen[p];
      if (!Number.isNaN(newest) && looked !== undefined && newest > looked) fresh.add(p);
    }
  }
  return fresh;
}
