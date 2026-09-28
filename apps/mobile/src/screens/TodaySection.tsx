import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { AppState, Pressable, StyleSheet, Text, View } from "react-native";
import type { Api, MorningView } from "../api";
import { formatDay, formatTime } from "../format";
import { colors, ui } from "../ui";

// The morning view (issue #38), fetched when Home mounts, when something changes, and
// when the app comes back to the foreground: the phone sleeps through the 04:00 build,
// so "today" has usually moved on since the last fetch.
export function useMorning(api: Api, refreshKey: number): MorningView | null {
  const [view, setView] = useState<MorningView | null>(null);

  const load = useCallback(async () => {
    try {
      setView(await api.getMorning());
    } catch {
      // Secondary to the capture list: on failure the section keeps its last state
      // (or stays hidden) rather than pushing a second error banner onto Home.
    }
  }, [api]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (state) => {
      if (state === "active") load();
    });
    return () => sub.remove();
  }, [load]);

  return view;
}

// Today's events and todos, then one resurfaced idea. No overdue labels and no counts,
// matching the server: a todo whose day has passed is simply still on the list.
export function TodaySection({ view, onOpen }: { view: MorningView; onOpen: (captureId: string) => void }) {
  // An item whose note was deleted (ADR 0022) has nothing to open.
  const open = (captureId: string | null) => (captureId ? () => onOpen(captureId) : undefined);
  const idea = view.resurfaced_idea;
  const empty = view.events.length === 0 && view.todos.length === 0 && !idea;
  if (empty) return null;

  return (
    <View style={styles.wrap}>
      <Text style={ui.label}>{formatDay(view.local_date)}</Text>
      {(view.events.length > 0 || view.todos.length > 0) && (
        <View style={styles.card}>
          {view.events.map((e) => (
            <Item
              key={e.id}
              icon="calendar-outline"
              title={e.title}
              detail={[formatTime(e.starts_at), e.location].filter(Boolean).join(" · ")}
              onPress={open(e.capture_id)}
            />
          ))}
          {view.todos.map((t) => (
            <Item
              key={t.id}
              icon="ellipse-outline"
              title={t.title}
              detail={t.notes}
              onPress={open(t.capture_id)}
            />
          ))}
        </View>
      )}
      {idea && (
        <Pressable
          onPress={open(idea.capture_id)}
          disabled={!idea.capture_id}
          style={({ pressed }) => [styles.card, styles.idea, pressed && styles.pressed]}
        >
          <Text style={ui.meta}>An idea from {formatDay(idea.captured_on, true)}</Text>
          <Text style={ui.rowTitle}>{idea.title}</Text>
          {idea.summary ? (
            <Text style={ui.bodyMuted} numberOfLines={3}>
              {idea.summary}
            </Text>
          ) : null}
        </Pressable>
      )}
    </View>
  );
}

function Item({
  icon,
  title,
  detail,
  onPress,
}: {
  icon: "calendar-outline" | "ellipse-outline";
  title: string;
  detail: string | null;
  onPress?: () => void;
}) {
  return (
    <Pressable onPress={onPress} disabled={!onPress} style={({ pressed }) => [styles.item, pressed && styles.pressed]}>
      <Ionicons name={icon} size={20} color={colors.muted} style={styles.icon} />
      <View style={{ flex: 1 }}>
        <Text style={styles.itemTitle}>{title}</Text>
        {detail ? (
          <Text style={ui.meta} numberOfLines={1}>
            {detail}
          </Text>
        ) : null}
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 20, paddingTop: 12, paddingBottom: 8 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 6,
    marginBottom: 12,
  },
  idea: { paddingVertical: 16, gap: 2 },
  item: { flexDirection: "row", alignItems: "flex-start", paddingVertical: 10 },
  icon: { marginRight: 12, marginTop: 2 },
  itemTitle: { color: colors.text, fontSize: 16, fontWeight: "500" },
  pressed: { opacity: 0.6 },
});
