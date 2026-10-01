import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, HabitDetail, HabitEvidence, Milestone } from "../api";
import { confirmDelete, IconCircle, Pill, SheetAction, SheetActions } from "../components";
import { ask, confirm } from "../dialogs";
import { useRefetchOnFocus } from "../focus";
import { formatDay, toIsoDate } from "../format";
import { haptic } from "../haptics";
import { celebrate } from "../motion";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";
import { lastTime, rhythm, TargetPicker, todayIndex, WeekDots } from "./HabitsScreen";

// Where a habit's days come from, said once, so a count is never a mystery.
const SOURCE: Record<HabitDetail["source"], string> = {
  activity:
    "Counted from your notes, the days you log, and workouts in Apple Health if it's on. Remove a day that shouldn't count.",
  family:
    "Counted from People: the days you saw, called or texted someone who's family. Mark who's family on their page.",
  bible: "Counted from Within: what you mark as read, notes that name a passage, and notes that say you read your Bible.",
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

  // A dot in this week's row: an empty past day is logged ("did it yesterday, forgot
  // to say"), a filled one is offered for removal. The server refuses a future day;
  // the row never offers one.
  const tapDay = (index: number) => {
    if (!habit) return;
    const date = new Date();
    date.setDate(date.getDate() - (todayIndex() - index));
    const iso = toIsoDate(date);
    if (habit.week_days[index]) return removeDay(iso);
    haptic.done();
    void run(`day:${iso}`, () => api.logHabit(id, iso));
  };

  const rename = async () => {
    if (!habit) return;
    const name = await ask({ title: "Name", initial: habit.name });
    if (name && name !== habit.name) void run("name", () => api.updateHabit(id, { name }));
  };

  const stop = async () => {
    if (!habit) return;
    const yes = await confirm({
      title: `Stop tracking ${habit.name}?`,
      message: "Its days stay in your notes, so tracking it again brings them back.",
      action: "Stop tracking",
      icon: "close-circle-outline",
    });
    if (!yes) return;
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

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <View style={{ flex: 1 }} />
        <IconCircle name="pencil" size={38} onPress={() => void rename()} accessibilityLabel="Rename" />
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
                <WeekDots days={habit.week_days} onDay={tapDay} />
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

            {!!habit.milestones?.length && (
              <View style={styles.section}>
                <Text style={ui.label}>Milestones</Text>
                {habit.milestones.map((m) => (
                  <MilestoneRow key={`${m.kind}-${m.title}`} milestone={m} onOpenCapture={onOpenCapture} />
                ))}
              </View>
            )}

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
              <SheetAction icon="close-circle-outline" label="Stop tracking" danger busy={busy === "stop"} onPress={() => void stop()} />
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
  health: "fitness-outline",
};

const MILESTONE_ICON: Record<Milestone["kind"], keyof typeof Ionicons.glyphMap> = {
  days: "calendar-number-outline",
  first: "flag-outline",
  longest: "trending-up-outline",
  heaviest: "barbell-outline",
};

function Evidence({ evidence, onOpenCapture }: { evidence: HabitEvidence; onOpenCapture: (id: string) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const text =
    evidence.kind === "logged"
      ? "Logged"
      : evidence.kind === "reading"
        ? `Read ${evidence.text ?? "the Bible"}`
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

// Something the habit reached (ADR 0050), noticed from the days and numbers memo has.
// A fact with its date, never a badge to chase.
function MilestoneRow({ milestone, onOpenCapture }: { milestone: Milestone; onOpenCapture: (id: string) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const capture = milestone.capture_id;
  return (
    <Pressable
      onPress={capture ? () => onOpenCapture(capture) : undefined}
      disabled={!capture}
      style={({ pressed }) => [styles.milestone, pressed && { opacity: 0.6 }]}
      accessibilityLabel={`${milestone.title}, ${milestone.detail}, ${formatDay(milestone.date, true)}`}
    >
      <View style={styles.milestoneIcon}>
        <Ionicons name={MILESTONE_ICON[milestone.kind]} size={18} color={colors.text} />
      </View>
      <View style={{ flex: 1 }}>
        <Text style={styles.dayTitle}>{milestone.title}</Text>
        <Text style={ui.meta}>
          {milestone.kind === "days" ? "" : `${milestone.detail} · `}
          {dayLabel(milestone.date)}
        </Text>
      </View>
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
  milestone: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline,
  },
  milestoneIcon: {
    width: 36,
    height: 36,
    borderRadius: 18,
    backgroundColor: colors.surfaceAlt,
    alignItems: "center",
    justifyContent: "center",
  },
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
