import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActionSheetIOS,
  Alert,
  type GestureResponderEvent,
  Platform,
  Pressable,
  ScrollView,
  Text,
  View,
} from "react-native";
import ReanimatedSwipeable, {
  SwipeDirection,
  type SwipeableMethods,
} from "react-native-gesture-handler/ReanimatedSwipeable";
import Animated, { interpolate, type SharedValue, useAnimatedStyle, useSharedValue, withSpring } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation } from "../api";
import { IconCircle, TickCircle } from "../components";
import { useRefetchOnFocus } from "../focus";
import { formatTime, toIsoDate } from "../format";
import { celebrate, celebrateAt, listLayout, springs } from "../motion";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";
import { DerivationSheet } from "./DerivationSheet";
import { haptic } from "../haptics";
import { usePullToRefresh } from "../refresh";

const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];
const CELL = 40;
const DOT = 32;

function startOfDay(d: Date): Date {
  const out = new Date(d);
  out.setHours(0, 0, 0, 0);
  return out;
}

function addDays(d: Date, n: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + n);
  return out;
}

// Monday of the week containing `d`.
function weekStart(d: Date): Date {
  const day = startOfDay(d);
  return addDays(day, -((day.getDay() + 6) % 7));
}

// The local calendar day an item belongs to, or null for an undated todo.
function dayOf(d: Derivation): string | null {
  const p = d.payload;
  if (d.kind === "todo") return typeof p.due === "string" ? p.due : null;
  if (d.kind === "event" && typeof p.starts_at === "string") {
    const t = new Date(p.starts_at);
    return Number.isNaN(t.getTime()) ? null : toIsoDate(t);
  }
  return null;
}

// Events on a day and a todo list, TickTick-style: pick a day in the week strip, see its
// events by time and its todos with checkboxes; undated todos live under Anytime. Ideas
// have no date, so they get their own list. No guilt mechanics: a todo whose day passed
// is carried onto today without an "overdue" label, and done items are never counted.
export function PlannerScreen({
  api,
  refreshKey,
  onBack,
  onIdeas,
  onOpenCapture,
  onChanged,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onIdeas: () => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const today = startOfDay(new Date());
  const todayKey = toIsoDate(today);
  const [selected, setSelected] = useState<Date>(today);
  const [items, setItems] = useState<Derivation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<{ kind: Derivation["kind"]; existing?: Derivation } | null>(null);

  const load = useCallback(async () => {
    try {
      const all = await api.listDerivations();
      setItems(all.filter((d) => d.status !== "rejected" && d.kind !== "idea"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);
  const pull = usePullToRefresh(load);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);
  useRefetchOnFocus(load);

  const week = useMemo(() => {
    const start = weekStart(selected);
    return Array.from({ length: 7 }, (_, i) => addDays(start, i));
  }, [selected]);

  const busyDays = useMemo(() => {
    const keys = new Set<string>();
    for (const d of items) {
      const key = dayOf(d);
      if (key && !(d.kind === "todo" && d.completed_at)) keys.add(key);
    }
    return keys;
  }, [items]);

  const selectedKey = toIsoDate(selected);
  const events = items
    .filter((d) => d.kind === "event" && dayOf(d) === selectedKey)
    .sort((a, b) => String(a.payload.starts_at).localeCompare(String(b.payload.starts_at)));
  const todos = items.filter((d) => {
    if (d.kind !== "todo") return false;
    const key = dayOf(d);
    if (key === selectedKey) return true;
    // Carried forward onto today, quietly, until it is done.
    return selectedKey === todayKey && key !== null && key < todayKey && !d.completed_at;
  });
  const openTodos = todos.filter((d) => !d.completed_at);
  const doneTodos = todos.filter((d) => d.completed_at);
  const anytime = items.filter((d) => d.kind === "todo" && dayOf(d) === null && !d.completed_at);

  // A tick from the checkbox (with a press, so fireflies rise from it) or from a swipe
  // (with the row's position). Clearing the day's last open todo gets the swarm.
  const toggle = async (d: Derivation, at?: { e?: GestureResponderEvent; x?: number; y?: number }) => {
    const completed = !d.completed_at;
    if (completed) {
      setTimeout(haptic.done, 90);
      const lastOfDay = openTodos.length === 1 && openTodos[0].id === d.id;
      if (lastOfDay) celebrate(at?.x ?? 200, at?.y ?? 400, true);
      else if (at?.e) celebrateAt(at.e);
      else if (at?.x != null && at.y != null) celebrate(at.x, at.y);
    } else haptic.pick();
    // Optimistic: the tick lands instantly; a failure puts it back.
    setItems((prev) =>
      prev.map((x) => (x.id === d.id ? { ...x, completed_at: completed ? new Date().toISOString() : null } : x)),
    );
    try {
      await api.reviewDerivation(d.id, { completed });
      onChanged();
    } catch (e) {
      setError(String(e));
      load();
    }
  };

  const add = () => {
    const pick = (kind: Derivation["kind"]) => setSheet({ kind });
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions(
        { options: ["Todo", "Event", "Cancel"], cancelButtonIndex: 2 },
        (i) => {
          if (i === 0) pick("todo");
          if (i === 1) pick("event");
        },
      );
      return;
    }
    Alert.alert("Add", undefined, [
      { text: "Todo", onPress: () => pick("todo") },
      { text: "Event", onPress: () => pick("event") },
      { text: "Cancel", style: "cancel" },
    ]);
  };

  const month = selected.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const dayTitle = selected.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  const nothing = events.length === 0 && todos.length === 0;

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title}>Planner</Text>
        <View style={{ flex: 1 }} />
        <IconCircle name="bulb-outline" size={38} onPress={onIdeas} />
        <IconCircle name="add" size={38} tone="light" onPress={add} />
      </View>

      <View style={[styles.monthRow, ui.pad]}>
        <Text style={styles.month}>{month}</Text>
        <View style={{ flex: 1 }} />
        {selectedKey !== todayKey && (
          <Pressable onPress={() => setSelected(today)} hitSlop={8}>
            <Text style={styles.todayLink}>Today</Text>
          </Pressable>
        )}
        <Pressable
          onPress={() => setSelected(addDays(selected, -7))}
          hitSlop={10}
          style={styles.arrow}
          accessibilityLabel="Previous week"
        >
          <Ionicons name="chevron-back" size={18} color={colors.muted} />
        </Pressable>
        <Pressable
          onPress={() => setSelected(addDays(selected, 7))}
          hitSlop={10}
          style={styles.arrow}
          accessibilityLabel="Next week"
        >
          <Ionicons name="chevron-forward" size={18} color={colors.muted} />
        </Pressable>
      </View>

      <WeekStrip
        week={week}
        selectedKey={selectedKey}
        todayKey={todayKey}
        busyDays={busyDays}
        onSelect={(day) => {
          haptic.pick();
          setSelected(day);
        }}
      />
      <View style={[ui.hairline, { marginHorizontal: 20 }]} />

      <ScrollView contentContainerStyle={[ui.pad, { paddingBottom: 40 + insets.bottom }]} refreshControl={pull}>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        <Text style={[ui.label, styles.section]}>{dayTitle}</Text>
        {nothing && <Text style={[ui.bodyMuted, styles.empty]}>Nothing on this day.</Text>}

        {events.map((d) => (
          <Animated.View key={d.id} layout={listLayout}>
            <Pressable
              onPress={() => setSheet({ kind: "event", existing: d })}
              style={({ pressed }) => [styles.eventRow, pressed && styles.pressed]}
            >
              <Text style={styles.time}>{formatTime(String(d.payload.starts_at))}</Text>
              <View style={styles.eventBar} />
              <View style={{ flex: 1 }}>
                <Text style={styles.itemText}>{String(d.payload.title ?? "")}</Text>
                <Meta d={d} />
              </View>
            </Pressable>
          </Animated.View>
        ))}

        {[...openTodos, ...doneTodos].map((d) => (
          <Animated.View key={d.id} layout={listLayout}>
            <TodoRow d={d} onToggle={toggle} onPress={() => setSheet({ kind: "todo", existing: d })} />
          </Animated.View>
        ))}

        {anytime.length > 0 && (
          <>
            <Text style={[ui.label, styles.section]}>Anytime</Text>
            {anytime.map((d) => (
              <Animated.View key={d.id} layout={listLayout}>
                <TodoRow d={d} onToggle={toggle} onPress={() => setSheet({ kind: "todo", existing: d })} />
              </Animated.View>
            ))}
          </>
        )}
      </ScrollView>

      {sheet && (
        <DerivationSheet
          api={api}
          captureId={null}
          kind={sheet.kind}
          existing={sheet.existing}
          initialDate={sheet.existing ? undefined : selected}
          onOpenNote={onOpenCapture}
          onClose={() => setSheet(null)}
          onSaved={() => {
            load();
            onChanged();
          }}
        />
      )}
    </View>
  );
}

// The week, with a marker that slides to the chosen day rather than jumping (ADR 0032:
// a choice between options glides).
function WeekStrip({
  week,
  selectedKey,
  todayKey,
  busyDays,
  onSelect,
}: {
  week: Date[];
  selectedKey: string;
  todayKey: string;
  busyDays: Set<string>;
  onSelect: (day: Date) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [width, setWidth] = useState(0);
  const index = week.findIndex((d) => toIsoDate(d) === selectedKey);
  const x = useSharedValue(0);
  const step = width > 0 ? (width - CELL) / 6 : 0;
  useEffect(() => {
    if (step > 0 && index >= 0) x.set(withSpring(index * step + (CELL - DOT) / 2, springs.release));
  }, [index, step, x]);
  const marker = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  // Three rows - letters, numbers, busy dots - so the marker lives in the numbers row
  // and lines up with them exactly.
  return (
    <View style={[styles.week, ui.pad]}>
      <View style={styles.weekRow}>
        {week.map((day, i) => (
          <Text key={toIsoDate(day)} style={[styles.dayCell, styles.dayLetter]}>
            {DAY_LETTERS[i]}
          </Text>
        ))}
      </View>
      <View style={styles.weekRow} onLayout={(e) => setWidth(e.nativeEvent.layout.width)}>
        {width > 0 && index >= 0 && <Animated.View style={[styles.marker, marker]} />}
        {week.map((day) => {
          const key = toIsoDate(day);
          const isSelected = key === selectedKey;
          const isToday = key === todayKey;
          return (
            <Pressable
              key={key}
              onPress={() => onSelect(day)}
              style={styles.dayCell}
              hitSlop={{ top: 18, bottom: 12 }}
              accessibilityRole="button"
              accessibilityState={{ selected: isSelected }}
              accessibilityLabel={day.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" })}
            >
              <View style={styles.dayNum}>
                <Text
                  style={[
                    styles.dayNumText,
                    isToday && !isSelected && { color: colors.record },
                    isSelected && { color: colors.bg },
                  ]}
                >
                  {day.getDate()}
                </Text>
              </View>
            </Pressable>
          );
        })}
      </View>
      <View style={styles.weekRow}>
        {week.map((day) => (
          <View key={toIsoDate(day)} style={styles.dayCell}>
            <View style={[styles.dot, busyDays.has(toIsoDate(day)) && styles.dotOn]} />
          </View>
        ))}
      </View>
    </View>
  );
}

// A todo swipes right to tick (or untick), done as soon as the row opens. The same is
// a VoiceOver action, and the checkbox and a tap still work as before.
function SwipeToTick({ children, onTick }: { children: React.ReactNode; onTick: (x: number, y: number) => void }) {
  const styles = useStyles();
  const swipe = useRef<SwipeableMethods>(null);
  const row = useRef<View>(null);
  return (
    <ReanimatedSwipeable
      ref={swipe}
      friction={1.6}
      leftThreshold={64}
      overshootLeft={false}
      renderLeftActions={(progress) => <DoneAction progress={progress} />}
      onSwipeableOpen={(direction) => {
        if (direction !== SwipeDirection.RIGHT) return;
        row.current?.measureInWindow((x, y, _w, h) => onTick(x + 32, y + h / 2));
        swipe.current?.close();
      }}
      containerStyle={styles.swipe}
    >
      <View
        ref={row}
        accessibilityActions={[{ name: "tick", label: "Mark done" }]}
        onAccessibilityAction={(e) => e.nativeEvent.actionName === "tick" && onTick(200, 400)}
      >
        {children}
      </View>
    </ReanimatedSwipeable>
  );
}

function DoneAction({ progress }: { progress: SharedValue<number> }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const animated = useAnimatedStyle(() => ({
    opacity: interpolate(progress.value, [0, 1], [0, 1], "clamp"),
    transform: [{ scale: interpolate(progress.value, [0, 1], [0.6, 1], "clamp") }],
  }));
  return (
    <View style={styles.doneWrap}>
      <Animated.View style={animated}>
        <Ionicons name="checkmark-circle" size={26} color={colors.record} />
      </Animated.View>
    </View>
  );
}

function TodoRow({
  d,
  onToggle,
  onPress,
}: {
  d: Derivation;
  onToggle: (d: Derivation, at?: { e?: GestureResponderEvent; x?: number; y?: number }) => void;
  onPress: () => void;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  const done = !!d.completed_at;
  const title = String(d.payload.title ?? "");
  return (
    <SwipeToTick onTick={(x, y) => onToggle(d, { x, y })}>
      <Pressable onPress={onPress} style={({ pressed }) => [styles.todoRow, pressed && styles.pressed]}>
        <TickCircle
          done={done}
          label={title}
          color={colors.muted}
          doneColor={colors.faint}
          onPress={(e) => onToggle(d, { e })}
        />
        <View style={{ flex: 1 }}>
          <Text style={[styles.itemText, done && styles.doneText]}>{title}</Text>
          {!done && <Meta d={d} />}
        </View>
      </Pressable>
    </SwipeToTick>
  );
}

// Secondary line: where it's from, and whether it still needs a look.
function Meta({ d }: { d: Derivation }) {
  const ui = useUi();
  const bits = [
    d.kind === "event" && typeof d.payload.location === "string" ? d.payload.location : null,
    d.status === "pending_review" ? "to review" : null,
  ].filter(Boolean);
  return bits.length ? <Text style={ui.meta}>{bits.join(" · ")}</Text> : null;
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: fonts.serif, fontSize: 30, color: colors.text, marginLeft: 4 },
  monthRow: { flexDirection: "row", alignItems: "center", marginTop: 14, gap: 4 },
  month: { color: colors.text, fontSize: 15, fontWeight: "600" },
  todayLink: { color: colors.record, fontSize: 14, fontWeight: "600", marginRight: 8 },
  arrow: { padding: 4 },
  week: { marginTop: 10, marginBottom: 10, gap: 4 },
  weekRow: { flexDirection: "row", justifyContent: "space-between" },
  marker: {
    position: "absolute",
    top: 0,
    left: 0,
    width: DOT,
    height: DOT,
    borderRadius: DOT / 2,
    backgroundColor: colors.text,
  },
  dayCell: { alignItems: "center", width: CELL },
  dayLetter: { color: colors.faint, fontSize: 11, fontWeight: "600", textAlign: "center" },
  dayNum: { width: DOT, height: DOT, borderRadius: DOT / 2, alignItems: "center", justifyContent: "center" },
  dayNumText: { color: colors.text, fontSize: 15, fontWeight: "500" },
  dot: { width: 4, height: 4, borderRadius: 2 },
  dotOn: { backgroundColor: colors.muted },
  section: { marginTop: 22, marginBottom: 6 },
  empty: { marginTop: 4 },
  swipe: { backgroundColor: colors.bg },
  eventRow: { flexDirection: "row", alignItems: "flex-start", gap: 10, paddingVertical: 9, backgroundColor: colors.bg },
  time: { color: colors.muted, fontSize: 14, width: 46, marginTop: 1, fontVariant: ["tabular-nums"] },
  eventBar: { width: 3, alignSelf: "stretch", borderRadius: 2, backgroundColor: colors.record },
  todoRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 9, backgroundColor: colors.bg },
  itemText: { color: colors.text, fontSize: 16 },
  doneText: { color: colors.faint, textDecorationLine: "line-through" },
  pressed: { opacity: 0.6 },
  doneWrap: { width: 64, justifyContent: "center", alignItems: "center" },
}));
