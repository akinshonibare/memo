import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation, Week } from "../api";
import { IconCircle } from "../components";
import { formatDue, formatWhen } from "../format";
import { haptic } from "../haptics";
import { colors, fonts, ui } from "../ui";

// The weekly review (ADR 0028; the brief's V3): Monday to Sunday, facts not a pep talk.
// Still-open todos are listed and can be ticked off here; they are never counted
// against you (no guilt mechanics).
export function ReviewScreen({
  api,
  onBack,
  onOpenCapture,
  onChanged,
}: {
  api: Api;
  onBack: () => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
}) {
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
  }, [load]);

  const tick = async (d: Derivation) => {
    haptic.done();
    try {
      await api.reviewDerivation(d.id, { completed: true });
      await load();
      onChanged();
    } catch (e) {
      setError(String(e));
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
          {(d) => <Line key={d.id} d={d} onTick={() => tick(d)} onPress={() => d.capture_id && onOpenCapture(d.capture_id)} />}
        </Section>

        <Section title="Coming up" items={week?.coming_up ?? []} empty="Nothing in the next seven days.">
          {(d) => <Line key={d.id} d={d} onPress={() => d.capture_id && onOpenCapture(d.capture_id)} />}
        </Section>

        <Section title="New ideas" items={week?.new_ideas ?? []} empty="No new ideas this week.">
          {(d) => <Line key={d.id} d={d} onPress={() => d.capture_id && onOpenCapture(d.capture_id)} />}
        </Section>
      </ScrollView>
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
  onTick?: () => void;
  onPress: () => void;
}) {
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
    <Pressable onPress={onPress} style={({ pressed }) => [styles.line, pressed && { opacity: 0.6 }]}>
      {d.kind === "todo" ? (
        <Pressable onPress={onTick} disabled={!onTick} hitSlop={10}>
          <Ionicons
            name={done ? "checkmark-circle" : "ellipse-outline"}
            size={21}
            color={done ? colors.green : colors.muted}
          />
        </Pressable>
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
  );
}

function short(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

const styles = StyleSheet.create({
  top: { flexDirection: "row", alignItems: "center", gap: 6, paddingTop: 4 },
  arrow: { padding: 6 },
  title: { fontFamily: fonts.serif, fontSize: 38, lineHeight: 44, color: colors.text, marginTop: 12 },
  summary: { color: colors.text, fontSize: 17, lineHeight: 24, marginTop: 16 },
  section: { marginTop: 28 },
  line: { flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 9 },
  lineTitle: { color: colors.text, fontSize: 16 },
  doneTitle: { color: colors.muted, textDecorationLine: "line-through" },
});
