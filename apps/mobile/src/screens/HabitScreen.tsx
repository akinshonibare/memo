import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActionSheetIOS, Alert, Platform, Pressable, ScrollView, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, HabitDetail, HabitEvidence } from "../api";
import { confirmDelete, IconCircle, Pill, SheetAction, SheetActions } from "../components";
import { useRefetchOnFocus } from "../focus";
import { formatDay, toIsoDate } from "../format";
import { haptic } from "../haptics";
import { celebrate } from "../motion";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";
import { lastTime, rhythm, TargetPicker, todayIndex, WeekDots } from "./HabitsScreen";

// Where a habit's days come from, said once, so a count is never a mystery.
const SOURCE: Record<HabitDetail["source"], string> = {
  activity: "Counted from your notes, and the days you log. Remove a day that shouldn't count.",
  family:
    "Counted from People: the days you saw, called or texted someone who's family. Mark who's family on their page.",
  bible: "Counted from Faith: readings you mark, notes that name a passage, and notes that say you read your Bible.",
};

// One habit (ADR 0043): this week, eight weeks at a glance, and every day that counted
// with what made it count. Rhythm, never a streak: nothing here resets.
export function HabitScreen({
  api,
  id,
  refreshKey,
  onBack,
  onOpenCapture,
  onChanged,
}: {
  api: Api;
  id: string;
  refreshKey: number;
  onBack: () => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [habit, setHabit] = useState<HabitDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const logButton = useRef<View>(null);

  const load = useCallback(async () => {
    try {
      setHabit(await api.getHabit(id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, id]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);
  useRefetchOnFocus(load);

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

  const doneToday = !!habit?.week_days[todayIndex()];
  const logToday = () => {
    if (doneToday) return;
    haptic.done();
    logButton.current?.measureInWindow((x, y, w, h) => celebrate(x + w / 2, y + h / 2));
    void run("log", () => api.logHabit(id));
  };

  // A day memo counted that shouldn't: its notes' activities are rejected, and the
  // same note read again won't bring it back.
  const removeDay = (date: string) =>
    confirmDelete(`Remove ${formatDay(date)}?`, () => void run(`day:${date}`, () => api.removeHabitDay(id, date)));

  const rename = () => {
    if (!habit || Platform.OS !== "ios") return;
    Alert.prompt(
      "Name",
      undefined,
      (value) => {
        const name = value?.trim();
        if (name) void run("name", () => api.updateHabit(id, { name }));
      },
      "plain-text",
      habit.name,
    );
  };

  const stop = () => {
    if (!habit) return;
    const go = async () => {
      setBusy("stop");
      try {
        await api.stopHabit(id);
        onChanged();
        onBack();
      } catch (e) {
        setError(String(e));
        setBusy(null);
      }
    };
    const message = "Its days stay in your notes, so tracking it again brings them back.";
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions(
        { title: `Stop tracking ${habit.name}?`, message, options: ["Stop tracking", "Cancel"], destructiveButtonIndex: 0, cancelButtonIndex: 1 },
        (i) => i === 0 && void go(),
      );
      return;
    }
    Alert.alert(`Stop tracking ${habit.name}?`, message, [
      { text: "Cancel", style: "cancel" },
      { text: "Stop tracking", style: "destructive", onPress: () => void go() },
    ]);
  };

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <View style={{ flex: 1 }} />
        {Platform.OS === "ios" && (
          <IconCircle name="pencil" size={38} onPress={rename} accessibilityLabel="Rename" />
        )}
      </View>
      <ScrollView contentContainerStyle={[ui.pad, { paddingBottom: 60 + insets.bottom }]}>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        {habit && (
          <>
            <Text style={[ui.h1, { marginTop: 0, marginBottom: 4 }]}>{habit.name}</Text>
            <Text style={ui.meta}>{lastTime(habit)}</Text>

            <View style={styles.card}>
              <Text style={styles.big}>{rhythm(habit)}</Text>
              <View style={{ marginTop: 14 }}>
                <WeekDots days={habit.week_days} />
              </View>
              <View style={styles.actions} ref={logButton} collapsable={false}>
                <Pill
                  label={doneToday ? "Done today" : "Log today"}
                  icon="checkmark"
                  tone="light"
                  grow
                  busy={busy === "log"}
                  disabled={doneToday}
                  onPress={logToday}
                />
              </View>
            </View>

            <View style={styles.card}>
              <Text style={ui.meta}>Eight weeks</Text>
              <Weeks weeks={habit.weeks} target={habit.target_per_week} />
              <Text style={[ui.meta, { marginTop: 10 }]}>
                {habit.since
                  ? `${habit.total_days} ${habit.total_days === 1 ? "day" : "days"} since ${formatDay(habit.since, true)}`
                  : "No days yet"}
              </Text>
            </View>

            <View style={styles.section}>
              <Text style={ui.label}>Aim for, days a week</Text>
              <TargetPicker
                value={habit.target_per_week}
                busy={busy === "target"}
                onChange={(n) => void run("target", () => api.updateHabit(id, { target_per_week: n }))}
              />
            </View>

            <View style={styles.section}>
              <Text style={ui.label}>Days</Text>
              <Text style={[ui.meta, styles.source]}>{SOURCE[habit.source]}</Text>
              {habit.days.length === 0 && <Text style={ui.bodyMuted}>Nothing in the last 60 days.</Text>}
              {habit.days.map((d) => (
                <View key={d.date} style={styles.day}>
                  <View style={styles.dayHead}>
                    <Text style={styles.dayTitle}>{dayLabel(d.date)}</Text>
                    {d.removable && (
                      <Pressable
                        onPress={() => removeDay(d.date)}
                        disabled={busy === `day:${d.date}`}
                        hitSlop={10}
                        accessibilityRole="button"
                        accessibilityLabel={`Remove ${formatDay(d.date)}`}
                      >
                        <Ionicons name="close-circle-outline" size={20} color={colors.faint} />
                      </Pressable>
                    )}
                  </View>
                  {d.evidence.map((e, i) => (
                    <Evidence key={i} evidence={e} onOpenCapture={onOpenCapture} />
                  ))}
                </View>
              ))}
            </View>

            <SheetActions>
              <SheetAction icon="close-circle-outline" label="Stop tracking" danger busy={busy === "stop"} onPress={stop} />
            </SheetActions>
          </>
        )}
      </ScrollView>
    </View>
  );
}

function dayLabel(date: string): string {
  const today = toIsoDate(new Date());
  const yesterday = toIsoDate(new Date(Date.now() - 86_400_000));
  if (date === today) return "Today";
  if (date === yesterday) return "Yesterday";
  return formatDay(date);
}

const EVIDENCE_ICON: Record<HabitEvidence["kind"], keyof typeof Ionicons.glyphMap> = {
  note: "document-text-outline",
  logged: "checkmark-circle-outline",
  contact: "person-outline",
  reading: "book-outline",
  passage: "bookmark-outline",
};

function Evidence({ evidence, onOpenCapture }: { evidence: HabitEvidence; onOpenCapture: (id: string) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const text =
    evidence.kind === "logged"
      ? "Logged"
      : evidence.kind === "reading"
        ? `Read ${evidence.text ?? "from the plan"}`
        : (evidence.text ?? "");
  const capture = evidence.capture_id;
  return (
    <Pressable
      onPress={capture ? () => onOpenCapture(capture) : undefined}
      disabled={!capture}
      style={({ pressed }) => [styles.evidence, pressed && { opacity: 0.6 }]}
    >
      <Ionicons name={EVIDENCE_ICON[evidence.kind]} size={16} color={colors.muted} style={{ marginTop: 2 }} />
      <Text style={[ui.bodyMuted, { flex: 1 }]} numberOfLines={2}>
        {text}
      </Text>
    </Pressable>
  );
}

// Days per week for the last eight, this week last, with the aim as a line if there is
// one. Facts to glance at: no week is marked as missed.
function Weeks({ weeks, target }: { weeks: number[]; target: number | null }) {
  const styles = useStyles();
  const HEIGHT = 84;
  const bar = (n: number) => Math.max(4, (n / 7) * HEIGHT);
  return (
    <View style={[styles.weeks, { height: HEIGHT + 22 }]}>
      {target != null && <View style={[styles.aim, { bottom: 22 + (target / 7) * HEIGHT }]} />}
      {weeks.map((n, i) => (
        <View key={i} style={styles.weekCol}>
          <View style={[styles.bar, { height: bar(n) }, n === 0 && styles.barEmpty, i === weeks.length - 1 && styles.barNow]} />
          <Text style={styles.weekLabel}>{n}</Text>
        </View>
      ))}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, paddingBottom: 8 },
  card: { backgroundColor: colors.surface, borderRadius: 20, padding: 16, marginTop: 20 },
  big: { fontFamily: fonts.serif, fontSize: 30, lineHeight: 36, color: colors.text },
  actions: { flexDirection: "row", marginTop: 16 },
  section: { marginTop: 28 },
  source: { marginBottom: 10, lineHeight: 19 },
  day: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: colors.hairline, gap: 6 },
  dayHead: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  dayTitle: { color: colors.text, fontSize: 16, fontWeight: "600" },
  evidence: { flexDirection: "row", gap: 8 },
  weeks: { flexDirection: "row", alignItems: "flex-end", gap: 8, marginTop: 12 },
  weekCol: { flex: 1, alignItems: "center", gap: 6 },
  bar: { width: "100%", borderRadius: 6, backgroundColor: colors.forest, opacity: 0.55 },
  barEmpty: { backgroundColor: colors.surfaceAlt, opacity: 1 },
  barNow: { opacity: 1 },
  weekLabel: { color: colors.faint, fontSize: 11, fontWeight: "600", height: 16 },
  aim: {
    position: "absolute",
    left: 0,
    right: 0,
    height: 1,
    borderStyle: "dashed",
    borderTopWidth: 1,
    borderColor: colors.muted,
  },
}));
