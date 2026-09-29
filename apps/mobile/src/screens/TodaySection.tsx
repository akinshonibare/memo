import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { AppState, type GestureResponderEvent, Pressable, Text, useWindowDimensions, View } from "react-native";
import Animated from "react-native-reanimated";
import type { Api, MorningView, OnThisDay, ReachOut } from "../api";
import { TickCircle } from "../components";
import { formatAgo, formatDay, formatTime, headline, lookBack } from "../format";
import { haptic } from "../haptics";
import { celebrate, celebrateAt, listLayout, rowExit } from "../motion";
import { makeStyles, useTheme } from "../theme";
import { useUi } from "../ui";
import { publishToday, type TodayProps } from "../widgets";

// What the Today widget shows: events by time, then todos, in the same order as here.
function widgetProps(view: MorningView): TodayProps {
  const lines: TodayProps["lines"] = [
    ...view.events.map((e) => ({ time: formatTime(e.starts_at), title: e.title, kind: "event" as const })),
    ...view.todos.map((t) => ({ time: null, title: t.title, kind: "todo" as const })),
  ];
  return { heading: formatDay(view.local_date), lines, more: Math.max(0, lines.length - 4) };
}

// The morning view (issue #38), fetched when Home mounts, when something changes, and
// when the app comes back to the foreground: the phone sleeps through the 04:00 build,
// so "today" has usually moved on since the last fetch.
export function useMorning(api: Api, refreshKey: number): MorningView | null {
  const [view, setView] = useState<MorningView | null>(null);

  const load = useCallback(async () => {
    try {
      const morning = await api.getMorning();
      setView(morning);
      publishToday(widgetProps(morning));
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

// A ticked todo stays a moment, checked, before it leaves the list.
const LINGER_MS = 650;

// Today's events and todos, one resurfaced idea, a note from this day in another year, then at most three
// people it may be time to get in touch with (ADR 0029). No overdue labels and no
// counts, matching the server: a todo whose day has passed is simply still on the
// list, and a person is a suggestion with a reason, not a debt.
export function TodaySection({
  view,
  onOpen,
  onOpenPerson,
  onTick,
  onSpoke,
  onNotNow,
}: {
  view: MorningView;
  onOpen: (captureId: string) => void;
  onOpenPerson: (personId: string) => void;
  onTick: (todoId: string) => Promise<void>;
  onSpoke: (personId: string) => Promise<void>;
  onNotNow: (personId: string) => Promise<void>;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const { width, height } = useWindowDimensions();
  // Gone at once when acted on; the refetch that follows agrees. A ticked todo lingers,
  // checked, then goes. Each is hidden only until a view arrives without it: after that
  // the server has it, so one unticked elsewhere later (Reminders, the Planner) comes
  // back rather than staying hidden.
  const [done, setDone] = useState<Set<string>>(new Set());
  const [acted, setActed] = useState<Map<string, "ticked" | "gone">>(new Map());
  const [seen, setSeen] = useState(view);
  if (seen !== view) {
    setSeen(view);
    const todoIds = new Set(view.todos.map((t) => t.id));
    const personIds = new Set((view.reach_out ?? []).map((r) => r.person_id));
    setActed((prev) => new Map([...prev].filter(([id]) => todoIds.has(id))));
    setDone((prev) => new Set([...prev].filter((id) => personIds.has(id))));
  }
  const ticked = (id: string) => acted.has(id);
  // An item whose note was deleted (ADR 0022) has nothing to open.
  const open = (captureId: string | null) => (captureId ? () => onOpen(captureId) : undefined);
  const events = view.events;
  const todos = view.todos.filter((t) => acted.get(t.id) !== "gone");
  const idea = view.resurfaced_idea;
  const lookedBack = view.on_this_day ?? null;
  const people = (view.reach_out ?? []).filter((r) => !done.has(r.person_id));
  const empty = events.length === 0 && todos.length === 0 && !idea && !lookedBack && people.length === 0;
  if (empty) return null;

  const act = (r: ReachOut, action: (id: string) => Promise<void>) => {
    setDone((prev) => new Set(prev).add(r.person_id));
    action(r.person_id).catch(() =>
      setDone((prev) => {
        const next = new Set(prev);
        next.delete(r.person_id);
        return next;
      }),
    );
  };

  const tick = (id: string, e: GestureResponderEvent) => {
    if (ticked(id)) return;
    const left = todos.filter((t) => t.id !== id && !ticked(t.id)).length;
    setActed((prev) => new Map(prev).set(id, "ticked"));
    setTimeout(haptic.done, 90); // on the landing of the pop, not the touch
    // The last thing on today's list gets the swarm.
    if (left === 0) celebrate(width / 2, height * 0.55, true);
    else celebrateAt(e);
    // Only if it's still ticked: a newer view may already have let it go.
    setTimeout(() => setActed((prev) => (prev.get(id) === "ticked" ? new Map(prev).set(id, "gone") : prev)), LINGER_MS);
    onTick(id).catch(() =>
      setActed((prev) => {
        const next = new Map(prev);
        next.delete(id);
        return next;
      }),
    );
  };

  return (
    <View style={styles.wrap}>
      <Text style={ui.label}>{formatDay(view.local_date)}</Text>
      {(events.length > 0 || todos.length > 0) && (
        <Animated.View style={styles.card} layout={listLayout}>
          {events.map((e) => (
            <Item
              key={e.id}
              icon="calendar-outline"
              title={e.title}
              detail={[formatTime(e.starts_at), e.location].filter(Boolean).join(" · ")}
              onPress={open(e.capture_id)}
            />
          ))}
          {todos.map((t) => (
            <Animated.View key={t.id} exiting={rowExit} layout={listLayout} style={styles.item}>
              <View style={styles.icon}>
                <TickCircle
                  done={ticked(t.id)}
                  size={20}
                  color={colors.muted}
                  doneColor={colors.record}
                  label={t.title}
                  onPress={(e) => tick(t.id, e)}
                />
              </View>
              <Pressable
                onPress={open(t.capture_id)}
                disabled={!t.capture_id}
                style={({ pressed }) => [{ flex: 1 }, pressed && styles.pressed]}
              >
                <Text style={[styles.itemTitle, ticked(t.id) && styles.itemDone]}>{t.title}</Text>
                {t.notes ? (
                  <Text style={ui.meta} numberOfLines={1}>
                    {t.notes}
                  </Text>
                ) : null}
              </Pressable>
            </Animated.View>
          ))}
        </Animated.View>
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
      {lookedBack && <LookBack note={lookedBack} today={view.local_date} onOpen={onOpen} />}
      {people.length > 0 && (
        <View style={[styles.card, styles.people]}>
          <Text style={ui.meta}>Maybe get in touch</Text>
          {people.map((r) => (
            <View key={r.person_id} style={styles.person}>
              <Pressable onPress={() => onOpenPerson(r.person_id)} style={({ pressed }) => pressed && styles.pressed}>
                <Text style={ui.rowTitle}>{r.name}</Text>
                <Text style={ui.meta}>In touch {formatAgo(r.last_contact_on)}</Text>
                {r.latest ? (
                  <Text style={ui.bodyMuted} numberOfLines={2}>
                    Last time: {r.latest}
                  </Text>
                ) : null}
              </Pressable>
              <View style={styles.personActions}>
                <Pressable
                  onPress={() => {
                    haptic.done();
                    act(r, onSpoke);
                  }}
                  style={({ pressed }) => [styles.pill, styles.pillLight, pressed && styles.pressed]}
                >
                  <Ionicons name="checkmark" size={16} color={colors.bg} />
                  <Text style={[styles.pillText, { color: colors.bg }]}>Spoke to them</Text>
                </Pressable>
                <Pressable
                  onPress={() => {
                    haptic.tap();
                    act(r, onNotNow);
                  }}
                  style={({ pressed }) => [styles.pill, pressed && styles.pressed]}
                >
                  <Text style={styles.pillText}>Not now</Text>
                </Pressable>
              </View>
            </View>
          ))}
        </View>
      )}
    </View>
  );
}

// A note from this day in another month or year (ADR 0040): one, never a feed.
function LookBack({ note, today, onOpen }: { note: OnThisDay; today: string; onOpen: (id: string) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { title, rest } = headline(note);
  return (
    <Pressable
      onPress={() => onOpen(note.capture_id)}
      style={({ pressed }) => [styles.card, styles.idea, pressed && styles.pressed]}
    >
      <Text style={ui.meta}>{lookBack(note.captured_on, today)}</Text>
      <Text style={ui.rowTitle}>{title}</Text>
      {rest ? (
        <Text style={ui.bodyMuted} numberOfLines={2}>
          {rest}
        </Text>
      ) : null}
    </Pressable>
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
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
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

const useStyles = makeStyles((colors) => ({
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
  itemDone: { color: colors.faint, textDecorationLine: "line-through" },
  pressed: { opacity: 0.6 },
  people: { paddingVertical: 16, gap: 2 },
  person: { paddingTop: 10, gap: 2 },
  personActions: { flexDirection: "row", gap: 8, marginTop: 10 },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.surfaceAlt,
    borderRadius: 999,
    paddingHorizontal: 14,
    height: 34,
  },
  pillLight: { backgroundColor: colors.text },
  pillText: { color: colors.text, fontSize: 14, fontWeight: "600" },
}));
