import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { Modal, Pressable, ScrollView, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { type Api, ApiError, type Habit, type Habits, type HabitSuggestion } from "../api";
import { IconCircle, Pill, SheetHandle, SheetHeader } from "../components";
import { useRefetchOnFocus } from "../focus";
import { formatAgo } from "../format";
import { haptic } from "../haptics";
import { useKeyboardHeight } from "../keyboard";
import { Dialogs } from "../dialogs";
import { LockCover } from "../lock";
import { celebrateAt } from "../motion";
import { usePullToRefresh } from "../refresh";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";

const DAYS = ["M", "T", "W", "T", "F", "S", "S"];

// Monday first, like the server's weeks.
export function todayIndex(): number {
  return (new Date().getDay() + 6) % 7;
}

// "2 of 3 this week", or "2 days this week" with no aim. Never a streak (ADR 0043).
export function rhythm(h: Pick<Habit, "this_week" | "target_per_week">): string {
  if (h.target_per_week) return `${h.this_week} of ${h.target_per_week} this week`;
  return `${h.this_week} ${h.this_week === 1 ? "day" : "days"} this week`;
}

export function lastTime(h: Pick<Habit, "last_done">): string {
  return h.last_done ? `Last time ${formatAgo(h.last_done)}` : "Not yet";
}

// Habits (ADR 0043): noticed in your notes, suggested once they repeat, and counted by
// the day. Turned on in Settings.
export function HabitsScreen({
  api,
  refreshKey,
  onBack,
  onOpenHabit,
  onChanged,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onOpenHabit: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [habits, setHabits] = useState<Habits | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setHabits(await api.getHabits());
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);
  useRefetchOnFocus(load);

  const pull = usePullToRefresh(load);

  const run = async (key: string, work: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await work();
      await load();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const accept = (s: HabitSuggestion) => {
    haptic.done();
    void run("track", () => api.trackHabit({ name: s.name, key: s.key }));
  };

  const notNow = (s: HabitSuggestion) => {
    haptic.tap();
    void run("dismiss", () => api.dismissHabit(s.key));
  };

  const log = (h: Habit) => {
    haptic.done();
    void run(`log:${h.id}`, () => api.logHabit(h.id));
  };

  const list = habits?.habits ?? [];
  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title}>Habits</Text>
        <View style={{ flex: 1 }} />
        <IconCircle name="add" size={38} tone="light" onPress={() => setAdding(true)} accessibilityLabel="Add a habit" />
      </View>

      <ScrollView
        contentContainerStyle={[ui.pad, { paddingBottom: 60 + insets.bottom }]}
        refreshControl={pull}
      >
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}

        {habits && !habits.enabled && (
          <Text style={[ui.bodyMuted, styles.hint]}>
            Habits is switched off for this account, so memo isn&apos;t reading your notes for what you do.
          </Text>
        )}

        {habits?.suggestion && (
          <View style={[styles.card, styles.suggestion]}>
            <Text style={ui.meta}>memo noticed</Text>
            <Text style={styles.suggestionTitle}>{habits.suggestion.name}</Text>
            <Text style={ui.bodyMuted}>
              {habits.suggestion.days} days in the last three weeks
              {habits.suggestion.total_days > habits.suggestion.days
                ? `, ${habits.suggestion.total_days} in all`
                : ""}
              . Keep track of it?
            </Text>
            <View style={styles.actions}>
              <Pill
                label="Track it"
                icon="add"
                tone="light"
                busy={busy === "track"}
                onPress={() => accept(habits.suggestion!)}
              />
              <Pill label="Not now" busy={busy === "dismiss"} onPress={() => notNow(habits.suggestion!)} />
            </View>
          </View>
        )}

        {habits && list.length === 0 && (
          <View style={styles.empty}>
            <Ionicons name="leaf-outline" size={28} color={colors.faint} />
            <Text style={[ui.bodyMuted, styles.hint]}>
              Nothing tracked yet. Write or say what you did - &quot;leg day at the gym&quot;, &quot;read 30 pages&quot; -
              and memo will suggest a habit once it&apos;s happened a few times. Or add one yourself.
            </Text>
          </View>
        )}

        {list.map((h) => (
          <HabitCard
            key={h.id}
            habit={h}
            busy={busy === `log:${h.id}`}
            onPress={() => onOpenHabit(h.id)}
            onLog={() => log(h)}
          />
        ))}
      </ScrollView>

      {adding && (
        <AddHabitSheet
          api={api}
          tracked={list}
          onClose={() => setAdding(false)}
          onAdded={(h) => {
            void load();
            onChanged();
            onOpenHabit(h.id);
          }}
        />
      )}
    </View>
  );
}

function HabitCard({
  habit,
  busy,
  onPress,
  onLog,
}: {
  habit: Habit;
  busy: boolean;
  onPress: () => void;
  onLog: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const doneToday = habit.week_days[todayIndex()];
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.card, styles.habit, pressed && { opacity: 0.7 }]}
      accessibilityRole="button"
    >
      <View style={styles.habitHead}>
        <View style={{ flex: 1 }}>
          <Text style={styles.habitName}>{habit.name}</Text>
          <Text style={ui.meta}>
            {rhythm(habit)} · {lastTime(habit)}
          </Text>
        </View>
        <Pressable
          onPress={(e) => {
            if (doneToday || busy) return;
            celebrateAt(e);
            onLog();
          }}
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel={doneToday ? `${habit.name}: done today` : `Log ${habit.name} today`}
          style={[styles.logButton, doneToday && styles.logDone]}
        >
          <Ionicons name="checkmark" size={20} color={doneToday ? colors.bg : colors.muted} />
        </Pressable>
      </View>
      <WeekDots days={habit.week_days} />
    </Pressable>
  );
}

// This week, Monday to Sunday: a filled dot for each day it happened, today ringed.
export function WeekDots({ days }: { days: boolean[] }) {
  const styles = useStyles();
  const today = todayIndex();
  return (
    <View style={styles.dots}>
      {DAYS.map((d, i) => (
        <View key={i} style={styles.dotCol}>
          <View style={[styles.dot, days[i] && styles.dotOn, i === today && styles.dotToday]} />
          <Text style={[styles.dotLabel, i === today && styles.dotLabelToday]}>{d}</Text>
        </View>
      ))}
    </View>
  );
}

// What someone might track, filed under the keys memo already reads notes into.
const PICKS = [
  "Gym",
  "Running",
  "Walking",
  "Reading",
  "Reading the Bible",
  "Prayer",
  "Time with family",
  "Journalling",
  "Meditation",
  "Cooking",
];

function AddHabitSheet({
  api,
  tracked,
  onClose,
  onAdded,
}: {
  api: Api;
  tracked: Habit[];
  onClose: () => void;
  onAdded: (h: Habit) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const keyboard = useKeyboardHeight();
  const [name, setName] = useState("");
  const [target, setTarget] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const have = new Set(tracked.map((h) => h.name.toLowerCase()));
  const picks = PICKS.filter((p) => !have.has(p.toLowerCase()));

  const save = async () => {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    setError(null);
    try {
      const made = await api.trackHabit({ name: n, target_per_week: target });
      haptic.done();
      onClose();
      onAdded(made);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" allowSwipeDismissal={!busy} onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: 24 + keyboard }]}>
        <SheetHandle />
        <SheetHeader
          onClose={onClose}
          action={<Pill label="Add" tone="light" onPress={() => void save()} busy={busy} disabled={!name.trim()} />}
        />
        <Text style={[ui.h2, styles.heading]}>New habit</Text>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 12 }}>
          <Text style={ui.label}>What</Text>
          <TextInput
            style={styles.input}
            value={name}
            onChangeText={setName}
            placeholder="Gym, reading, early nights"
            placeholderTextColor={colors.muted}
            autoFocus
            maxLength={80}
            returnKeyType="done"
            onSubmitEditing={() => void save()}
          />
          {picks.length > 0 && (
            <View style={styles.chips}>
              {picks.map((p) => (
                <Pressable
                  key={p}
                  onPress={() => {
                    haptic.tap();
                    setName(p);
                  }}
                  style={[styles.chip, name === p && styles.chipOn]}
                  accessibilityRole="button"
                >
                  <Text style={[styles.chipText, name === p && styles.chipTextOn]}>{p}</Text>
                </Pressable>
              ))}
            </View>
          )}
          <Text style={ui.label}>Aim for, days a week</Text>
          <TargetPicker value={target} onChange={setTarget} />
          <Text style={[ui.meta, { marginTop: 12, lineHeight: 19 }]}>
            memo counts the days your notes say you did it, and you can log a day yourself. An aim is only for
            you: nothing is ever overdue.
          </Text>
        </ScrollView>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

// No aim, or 1 to 7 days a week.
export function TargetPicker({
  value,
  onChange,
  busy,
}: {
  value: number | null;
  onChange: (n: number | null) => void;
  busy?: boolean;
}) {
  const styles = useStyles();
  const options: (number | null)[] = [null, 1, 2, 3, 4, 5, 6, 7];
  return (
    <View style={styles.targets}>
      {options.map((n) => (
        <Pressable
          key={n ?? "none"}
          disabled={busy}
          onPress={() => {
            haptic.tap();
            onChange(n);
          }}
          style={[styles.target, n === null && styles.targetWide, value === n && styles.chipOn]}
          accessibilityRole="button"
          accessibilityLabel={n === null ? "No aim" : `${n} a week`}
        >
          <Text style={[styles.chipText, value === n && styles.chipTextOn]}>{n === null ? "None" : n}</Text>
        </Pressable>
      ))}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, paddingBottom: 8 },
  title: { fontFamily: fonts.serif, fontSize: 30, color: colors.text, marginLeft: 4 },
  hint: { lineHeight: 22, marginTop: 12 },
  card: { backgroundColor: colors.surface, borderRadius: 20, paddingHorizontal: 16, marginTop: 12 },
  suggestion: { paddingVertical: 16, gap: 4 },
  suggestionTitle: { fontFamily: fonts.serif, fontSize: 28, color: colors.text },
  actions: { flexDirection: "row", gap: 10, marginTop: 12 },
  empty: { alignItems: "center", paddingTop: 40, paddingHorizontal: 12 },
  habit: { paddingVertical: 16, gap: 14 },
  habitHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  habitName: { color: colors.text, fontSize: 18, fontWeight: "600", marginBottom: 2 },
  logButton: {
    width: 40,
    height: 40,
    borderRadius: 20,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceAlt,
  },
  logDone: { backgroundColor: colors.forest },
  dots: { flexDirection: "row", justifyContent: "space-between" },
  dotCol: { alignItems: "center", gap: 6, flex: 1 },
  dot: { width: 14, height: 14, borderRadius: 7, backgroundColor: colors.surfaceAlt },
  dotOn: { backgroundColor: colors.forest },
  dotToday: { borderWidth: 2, borderColor: colors.text },
  dotLabel: { color: colors.faint, fontSize: 11, fontWeight: "600" },
  dotLabelToday: { color: colors.text },
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  heading: { marginTop: 12, marginBottom: 6 },
  input: {
    fontSize: 16,
    color: colors.text,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 16,
    backgroundColor: colors.surfaceAlt,
    marginBottom: 12,
  },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginBottom: 20 },
  chip: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 999, backgroundColor: colors.surfaceAlt },
  chipOn: { backgroundColor: colors.text },
  chipText: { color: colors.text, fontSize: 14, fontWeight: "600" },
  chipTextOn: { color: colors.bg },
  targets: { flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 6 },
  target: {
    minWidth: 34,
    height: 34,
    borderRadius: 17,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surfaceAlt,
  },
  targetWide: { paddingHorizontal: 12 },
}));
