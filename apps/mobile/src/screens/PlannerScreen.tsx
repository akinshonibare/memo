import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActionSheetIOS, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation } from "../api";
import { IconCircle } from "../components";
import { formatTime, toIsoDate } from "../format";
import { colors, ui } from "../ui";
import { DerivationSheet } from "./DerivationSheet";

const DAY_LETTERS = ["M", "T", "W", "T", "F", "S", "S"];

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

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

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

  const toggle = async (d: Derivation) => {
    const completed = !d.completed_at;
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
        <Pressable onPress={() => setSelected(addDays(selected, -7))} hitSlop={10} style={styles.arrow}>
          <Ionicons name="chevron-back" size={18} color={colors.muted} />
        </Pressable>
        <Pressable onPress={() => setSelected(addDays(selected, 7))} hitSlop={10} style={styles.arrow}>
          <Ionicons name="chevron-forward" size={18} color={colors.muted} />
        </Pressable>
      </View>

      <View style={[styles.week, ui.pad]}>
        {week.map((day, i) => {
          const key = toIsoDate(day);
          const isSelected = key === selectedKey;
          const isToday = key === todayKey;
          return (
            <Pressable key={key} onPress={() => setSelected(day)} style={styles.dayCell}>
              <Text style={styles.dayLetter}>{DAY_LETTERS[i]}</Text>
              <View style={[styles.dayNum, isSelected && styles.dayNumSelected]}>
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
              <View style={[styles.dot, busyDays.has(key) && styles.dotOn]} />
            </Pressable>
          );
        })}
      </View>
      <View style={[ui.hairline, { marginHorizontal: 20 }]} />

      <ScrollView contentContainerStyle={[ui.pad, { paddingBottom: 40 + insets.bottom }]}>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        <Text style={[ui.label, styles.section]}>{dayTitle}</Text>
        {nothing && <Text style={[ui.bodyMuted, styles.empty]}>Nothing on this day.</Text>}

        {events.map((d) => (
          <Pressable
            key={d.id}
            onPress={() => setSheet({ kind: d.kind, existing: d })}
            style={({ pressed }) => [styles.eventRow, pressed && styles.pressed]}
          >
            <Text style={styles.time}>{formatTime(String(d.payload.starts_at))}</Text>
            <View style={styles.eventBar} />
            <View style={{ flex: 1 }}>
              <Text style={styles.itemText}>{String(d.payload.title ?? "")}</Text>
              <Meta d={d} />
            </View>
          </Pressable>
        ))}

        {[...openTodos, ...doneTodos].map((d) => (
          <TodoRow key={d.id} d={d} onToggle={toggle} onPress={() => setSheet({ kind: "todo", existing: d })} />
        ))}

        {anytime.length > 0 && (
          <>
            <Text style={[ui.label, styles.section]}>Anytime</Text>
            {anytime.map((d) => (
              <TodoRow key={d.id} d={d} onToggle={toggle} onPress={() => setSheet({ kind: "todo", existing: d })} />
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

function TodoRow({
  d,
  onToggle,
  onPress,
}: {
  d: Derivation;
  onToggle: (d: Derivation) => void;
  onPress: () => void;
}) {
  const done = !!d.completed_at;
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.todoRow, pressed && styles.pressed]}>
      <Pressable onPress={() => onToggle(d)} hitSlop={10}>
        <Ionicons
          name={done ? "checkmark-circle" : "ellipse-outline"}
          size={22}
          color={done ? colors.faint : colors.muted}
        />
      </Pressable>
      <View style={{ flex: 1 }}>
        <Text style={[styles.itemText, done && styles.doneText]}>{String(d.payload.title ?? "")}</Text>
        {!done && <Meta d={d} />}
      </View>
    </Pressable>
  );
}

// Secondary line: where it's from, and whether it still needs a look.
function Meta({ d }: { d: Derivation }) {
  const bits = [
    d.kind === "event" && typeof d.payload.location === "string" ? d.payload.location : null,
    d.status === "pending_review" ? "to review" : null,
  ].filter(Boolean);
  return bits.length ? <Text style={ui.meta}>{bits.join(" · ")}</Text> : null;
}

const styles = StyleSheet.create({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: "InstrumentSerif_400Regular", fontSize: 30, color: colors.text, marginLeft: 4 },
  monthRow: { flexDirection: "row", alignItems: "center", marginTop: 14, gap: 4 },
  month: { color: colors.text, fontSize: 15, fontWeight: "600" },
  todayLink: { color: colors.record, fontSize: 14, fontWeight: "600", marginRight: 8 },
  arrow: { padding: 4 },
  week: { flexDirection: "row", justifyContent: "space-between", marginTop: 10, marginBottom: 10 },
  dayCell: { alignItems: "center", width: 40, gap: 4 },
  dayLetter: { color: colors.faint, fontSize: 11, fontWeight: "600" },
  dayNum: { width: 32, height: 32, borderRadius: 16, alignItems: "center", justifyContent: "center" },
  dayNumSelected: { backgroundColor: colors.text },
  dayNumText: { color: colors.text, fontSize: 15, fontWeight: "500" },
  dot: { width: 4, height: 4, borderRadius: 2 },
  dotOn: { backgroundColor: colors.muted },
  section: { marginTop: 22, marginBottom: 6 },
  empty: { marginTop: 4 },
  eventRow: { flexDirection: "row", alignItems: "flex-start", gap: 10, paddingVertical: 9 },
  time: { color: colors.muted, fontSize: 14, width: 46, marginTop: 1, fontVariant: ["tabular-nums"] },
  eventBar: { width: 3, alignSelf: "stretch", borderRadius: 2, backgroundColor: colors.record },
  todoRow: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 9 },
  itemText: { color: colors.text, fontSize: 16 },
  doneText: { color: colors.faint, textDecorationLine: "line-through" },
  pressed: { opacity: 0.6 },
});
