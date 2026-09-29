import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { type GestureResponderEvent, Pressable, ScrollView, Text, useWindowDimensions, View } from "react-native";
import Animated from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation, FaithWeek, HabitWeek, Week } from "../api";
import { asTranslation, openInBible } from "../bible";
import { IconCircle, TickCircle } from "../components";
import { useRefetchOnFocus } from "../focus";
import { formatDue, formatWhen } from "../format";
import { haptic } from "../haptics";
import { celebrate, celebrateAt, listLayout, rowExit } from "../motion";
import { fonts, useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";

// The weekly review (ADR 0028; the brief's V3): Monday to Sunday, facts not a pep talk.
// Still-open todos are listed and can be ticked off here; they are never counted
// against you (no guilt mechanics).
export function ReviewScreen({
  api,
  refreshKey,
  onBack,
  onOpenCapture,
  onChanged,
  translation,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
  translation?: string; // what a passage opens in (ADR 0042)
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [weeksAgo, setWeeksAgo] = useState(0);
  const [week, setWeek] = useState<Week | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setWeek(await api.weekReview(weeksAgo));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, weeksAgo]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);
  useRefetchOnFocus(load);

  // Ticked here, it pops and leaves the list; ticking the last open one empties the
  // week's list, and that gets the swarm.
  const { width, height } = useWindowDimensions();
  const [ticked, setTicked] = useState<Set<string>>(new Set());
  // Shown ticked until a week arrives without it; after that, one unticked elsewhere
  // (Reminders, Today) shows open again rather than staying checked.
  const [seen, setSeen] = useState(week);
  if (seen !== week) {
    setSeen(week);
    const open = new Set((week?.still_open ?? []).map((d) => d.id));
    setTicked((prev) => new Set([...prev].filter((id) => open.has(id))));
  }
  const tick = async (d: Derivation, e: GestureResponderEvent) => {
    if (ticked.has(d.id)) return;
    setTimeout(haptic.done, 90);
    const open = (week?.still_open ?? []).filter((x) => !ticked.has(x.id));
    if (open.length === 1 && week?.still_open_total === 1) celebrate(width / 2, height * 0.55, true);
    else celebrateAt(e);
    setTicked((prev) => new Set(prev).add(d.id));
    try {
      await api.reviewDerivation(d.id, { completed: true });
      setTimeout(() => void load(), 600);
      onChanged();
    } catch (err) {
      setTicked((prev) => {
        const next = new Set(prev);
        next.delete(d.id);
        return next;
      });
      setError(String(err));
    }
  };

  const range = week ? `${short(week.start)} – ${short(week.end)}` : "";
  const c = week?.captured;
  const total = c ? c.notes + c.voice + c.photos : 0;

  return (
    <View style={ui.screen}>
      <View style={[styles.top, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <View style={{ flex: 1 }} />
        <Pressable onPress={() => setWeeksAgo((w) => Math.min(52, w + 1))} hitSlop={10} style={styles.arrow}>
          <Ionicons name="chevron-back" size={18} color={colors.muted} />
        </Pressable>
        <Pressable
          onPress={() => setWeeksAgo((w) => Math.max(0, w - 1))}
          disabled={weeksAgo === 0}
          hitSlop={10}
          style={styles.arrow}
        >
          <Ionicons name="chevron-forward" size={18} color={weeksAgo === 0 ? colors.hairline : colors.muted} />
        </Pressable>
      </View>
      <ScrollView contentContainerStyle={[ui.pad, { paddingBottom: 48 + insets.bottom }]}>
        <Text style={styles.title}>{weeksAgo === 0 ? "This week" : weeksAgo === 1 ? "Last week" : `${weeksAgo} weeks ago`}</Text>
        <Text style={ui.meta}>{range}</Text>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}

        {week && (
          <Text style={styles.summary}>
            {total === 0
              ? "Nothing captured this week."
              : `You captured ${plural(c!.notes, "note")}${c!.voice ? `, ${plural(c!.voice, "voice note")}` : ""}${
                  c!.photos ? ` and ${plural(c!.photos, "photo")}` : ""
                }.`}
          </Text>
        )}

        <Section title="Done" items={week?.done ?? []} empty="Nothing ticked off yet.">
          {(d) => <Line key={d.id} d={d} done onPress={() => d.capture_id && onOpenCapture(d.capture_id)} />}
        </Section>

        <Section
          title={week && week.still_open_total > week.still_open.length ? `Still open · ${week.still_open.length} of ${week.still_open_total}` : "Still open"}
          items={week?.still_open ?? []}
          empty="Nothing open. Enjoy it."
        >
          {(d) => (
            <Line
              key={d.id}
              d={d}
              done={ticked.has(d.id)}
              onTick={(e) => tick(d, e)}
              onPress={() => d.capture_id && onOpenCapture(d.capture_id)}
            />
          )}
        </Section>

        <Section title="Coming up" items={week?.coming_up ?? []} empty="Nothing in the next seven days.">
          {(d) => <Line key={d.id} d={d} onPress={() => d.capture_id && onOpenCapture(d.capture_id)} />}
        </Section>

        <Section title="New ideas" items={week?.new_ideas ?? []} empty="No new ideas this week.">
          {(d) => <Line key={d.id} d={d} onPress={() => d.capture_id && onOpenCapture(d.capture_id)} />}
        </Section>

        {week?.habits && week.habits.length > 0 && <HabitsSection habits={week.habits} />}

        {week?.faith && (
          <FaithSection faith={week.faith} translation={translation} onOpenCapture={onOpenCapture} />
        )}
      </ScrollView>
    </View>
  );
}

// The week in Faith (ADR 0042), its own section at the end, only while Faith is on.
// What was read, which prayers were answered, and the passages written about: facts,
// nothing to beat.
function FaithSection({
  faith,
  translation,
  onOpenCapture,
}: {
  faith: FaithWeek;
  translation?: string;
  onOpenCapture: (id: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const nothing = faith.read.length === 0 && faith.answered.length === 0 && faith.passages.length === 0;
  return (
    <View style={styles.section}>
      <Text style={ui.label}>Faith</Text>
      {nothing && <Text style={ui.bodyMuted}>A quiet week here.</Text>}
      {faith.read.length > 0 && (
        <View style={styles.line}>
          <Ionicons name="book-outline" size={18} color={colors.muted} style={{ marginTop: 2 }} />
          <Text style={[styles.lineTitle, { flex: 1 }]}>Read {faith.read.map((r) => r.label).join(", ")}</Text>
        </View>
      )}
      {faith.answered.map((p) => (
        <View key={p.id} style={styles.line}>
          <Ionicons name="checkmark-circle" size={18} color={colors.forest} style={{ marginTop: 2 }} />
          <View style={{ flex: 1 }}>
            <Text style={styles.lineTitle}>{p.title}</Text>
            <Text style={ui.meta}>{p.answer ? `Answered. ${p.answer}` : "Answered"}</Text>
          </View>
        </View>
      ))}
      {faith.passages.map(({ passage, capture_id }) => (
        <Pressable
          key={passage.ref}
          onPress={() => (capture_id ? onOpenCapture(capture_id) : openInBible(passage, asTranslation(translation)))}
          style={({ pressed }) => [styles.line, pressed && { opacity: 0.6 }]}
        >
          <Ionicons name="bookmark-outline" size={18} color={colors.muted} style={{ marginTop: 2 }} />
          <View style={{ flex: 1 }}>
            <Text style={styles.lineTitle}>{passage.ref}</Text>
            <Text style={ui.meta}>You wrote about it</Text>
          </View>
        </Pressable>
      ))}
    </View>
  );
}

// Each habit's days that week (ADR 0043): a count against the aim if there is one, and
// no verdict either way.
function HabitsSection({ habits }: { habits: HabitWeek[] }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <View style={styles.section}>
      <Text style={ui.label}>Habits</Text>
      {habits.map((h) => (
        <View key={h.habit_id} style={styles.line}>
          <Ionicons name="leaf-outline" size={18} color={colors.muted} style={{ marginTop: 2 }} />
          <Text style={[styles.lineTitle, { flex: 1 }]}>{h.name}</Text>
          <Text style={ui.meta}>
            {h.target_per_week
              ? `${h.days} of ${h.target_per_week}`
              : `${h.days} ${h.days === 1 ? "day" : "days"}`}
          </Text>
        </View>
      ))}
    </View>
  );
}

function Section({
  title,
  items,
  empty,
  children,
}: {
  title: string;
  items: Derivation[];
  empty: string;
  children: (d: Derivation) => React.ReactNode;
}) {
  const ui = useUi();
  const styles = useStyles();
  return (
    <View style={styles.section}>
      <Text style={ui.label}>{title}</Text>
      {items.length === 0 ? <Text style={ui.bodyMuted}>{empty}</Text> : items.map(children)}
    </View>
  );
}

function Line({
  d,
  done,
  onTick,
  onPress,
}: {
  d: Derivation;
  done?: boolean;
  onTick?: (e: GestureResponderEvent) => void;
  onPress: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const p = d.payload;
  const detail =
    d.kind === "event" && typeof p.starts_at === "string"
      ? formatWhen(p.starts_at)
      : d.kind === "todo" && typeof p.due === "string"
        ? `Due ${formatDue(p.due)}`
        : d.kind === "idea" && typeof p.summary === "string"
          ? p.summary
          : null;
  return (
    <Animated.View exiting={rowExit} layout={listLayout}>
    <Pressable onPress={onPress} style={({ pressed }) => [styles.line, pressed && { opacity: 0.6 }]}>
      {d.kind === "todo" ? (
        <TickCircle
          done={!!done}
          label={String(p.title ?? "")}
          size={21}
          color={colors.muted}
          doneColor={colors.green}
          onPress={done ? undefined : onTick}
        />
      ) : (
        <Ionicons name={d.kind === "event" ? "calendar-outline" : "bulb-outline"} size={19} color={colors.muted} />
      )}
      <View style={{ flex: 1 }}>
        <Text style={[styles.lineTitle, done && styles.doneTitle]}>{String(p.title ?? "")}</Text>
        {detail ? (
          <Text style={ui.meta} numberOfLines={2}>
            {detail}
          </Text>
        ) : null}
      </View>
    </Pressable>
    </Animated.View>
  );
}

function short(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

const useStyles = makeStyles((colors) => ({
  top: { flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 4 },
  arrow: { padding: 6 },
  title: { fontFamily: fonts.serif, fontSize: 38, lineHeight: 44, color: colors.text, marginTop: 12 },
  summary: { color: colors.text, fontSize: 17, lineHeight: 24, marginTop: 16 },
  section: { marginTop: 28 },
  line: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 9 },
  lineTitle: { color: colors.text, fontSize: 16 },
  doneTitle: { color: colors.muted, textDecorationLine: "line-through" },
}));
