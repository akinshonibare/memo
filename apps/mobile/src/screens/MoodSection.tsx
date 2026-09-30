import { useCallback, useEffect, useState } from "react";
import { Modal, Pressable, ScrollView, Text, View } from "react-native";
import { type Api, type MoodDay, type MoodDayDetail, type MoodWeek, type Valence } from "../api";
import { SheetHandle, SheetHeader } from "../components";
import { Dialogs } from "../dialogs";
import { formatDay, formatStamp, toIsoDate } from "../format";
import { haptic } from "../haptics";
import { LockCover } from "../lock";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";

// Mood, in Within (ADR 0058): how you've sounded in your notes, as seven dots and one
// sentence made from them. memo's read of a note is a guess, so a day opens onto the
// notes behind it and the phrase from each, and your own word for the day outranks the
// reads. No score, no streak, nothing sent anywhere: it describes how the notes sound.

export const STEPS: { valence: Valence; name: string }[] = [
  { valence: -2, name: "Low" },
  { valence: -1, name: "Heavy" },
  { valence: 0, name: "Steady" },
  { valence: 1, name: "Good" },
  { valence: 2, name: "Bright" },
];

export function nameOf(valence: Valence): string {
  return STEPS.find((s) => s.valence === valence)?.name ?? "";
}

// Cool for the heavy end, the theme's moss and amber for the bright end. The palettes
// have no cool colour of their own, so the two blues are chosen per scheme.
export function useMoodColour(): (valence: Valence | null) => string {
  const { colors } = useTheme();
  const dark = colors.scheme === "dark";
  return (valence) => {
    switch (valence) {
      case -2:
        return dark ? "#4C73A3" : "#2D5485";
      case -1:
        return dark ? "#93A9BD" : "#7391AB";
      case 0:
        return colors.muted;
      case 1:
        return colors.green;
      case 2:
        return colors.record;
      default:
        return "transparent";
    }
  };
}

const INITIALS = ["S", "M", "T", "W", "T", "F", "S"];

function weekday(isoDay: string): number {
  return new Date(`${isoDay}T00:00:00`).getDay();
}

// The week, at the top of Within.
export function MoodCard({ week, onOpenDay }: { week: MoodWeek; onOpenDay: (day: string) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const colour = useMoodColour();
  const today = toIsoDate(new Date());
  return (
    <View style={styles.card}>
      <Text style={ui.meta}>{"How you've been"}</Text>
      <View style={styles.dots}>
        {week.days.map((d) => (
          <Pressable
            key={d.day}
            onPress={() => {
              haptic.tap();
              onOpenDay(d.day);
            }}
            hitSlop={6}
            style={styles.dayCol}
            accessibilityRole="button"
            accessibilityLabel={`${formatDay(d.day)}: ${
              d.valence === null ? "nothing said" : `${nameOf(d.valence)}${d.yours ? ", in your words" : ""}`
            }`}
          >
            <View
              style={[
                styles.dot,
                d.valence === null
                  ? { borderWidth: 1, borderColor: colors.faint, borderStyle: d.day === today ? "dashed" : "solid" }
                  : { backgroundColor: colour(d.valence) },
                d.yours && { borderWidth: 2, borderColor: colors.text },
              ]}
            />
            <Text style={[ui.meta, d.day === today && { color: colors.text }]}>{INITIALS[weekday(d.day)]}</Text>
          </Pressable>
        ))}
      </View>
      <Text style={styles.summary}>
        {week.summary ?? "memo hears how you sound as you write. The week fills in from your notes."}
      </Text>
    </View>
  );
}

// One day: how it reads, the notes behind it with the phrase from each, and five
// words to say how it really was.
export function MoodDaySheet({
  api,
  day,
  onClose,
  onChanged,
  onOpenNote,
}: {
  api: Api;
  day: string;
  onClose: () => void;
  onChanged: () => void;
  onOpenNote: (captureId: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const colour = useMoodColour();
  const [detail, setDetail] = useState<MoodDayDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    try {
      setDetail(await api.moodDay(day));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, day]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load]);

  const say = async (valence: Valence | null) => {
    if (busy) return;
    setBusy(true);
    try {
      setDetail(await api.sayMood(day, valence));
      haptic.tap();
      setError(null);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  };

  const shown: MoodDay | undefined = detail?.day;
  const reads = detail?.reads ?? [];
  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.sheet}>
        <SheetHandle />
        <SheetHeader onClose={onClose} action={null} />
        <ScrollView contentContainerStyle={{ paddingBottom: 40 }}>
          <Text style={ui.meta}>{day === toIsoDate(new Date()) ? "Today" : formatDay(day)}</Text>
          <Text style={styles.heading}>
            {!shown
              ? " "
              : shown.valence === null
                ? "Nothing said this day"
                : `${shown.yours ? "You said" : "Sounded"} ${nameOf(shown.valence).toLowerCase()}`}
          </Text>
          {shown && shown.valence !== null && !shown.yours && shown.words.length > 0 && (
            <Text style={ui.bodyMuted}>{shown.words.join(", ")}</Text>
          )}
          {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}

          <Text style={[ui.label, styles.section]}>How was the day?</Text>
          <View style={styles.steps}>
            {STEPS.map((s) => {
              const picked = !!shown?.yours && shown.valence === s.valence;
              return (
                <Pressable
                  key={s.valence}
                  onPress={() => void say(s.valence)}
                  disabled={busy}
                  style={({ pressed }) => [
                    styles.step,
                    picked && { backgroundColor: colors.text },
                    pressed && { opacity: 0.6 },
                  ]}
                  accessibilityRole="button"
                  accessibilityState={{ selected: picked }}
                >
                  <View style={[styles.stepDot, { backgroundColor: colour(s.valence) }]} />
                  <Text style={[styles.stepText, picked && { color: colors.bg }]}>{s.name}</Text>
                </Pressable>
              );
            })}
          </View>
          {shown?.yours && (
            <Pressable onPress={() => void say(null)} hitSlop={8} style={{ marginTop: 12 }} accessibilityRole="button">
              <Text style={styles.link}>{reads.length ? "Go back to memo's read" : "Clear"}</Text>
            </Pressable>
          )}

          {reads.length > 0 && (
            <>
              <Text style={[ui.label, styles.section]}>From your notes</Text>
              {reads.map((r) => (
                <Pressable
                  key={r.capture_id}
                  onPress={() => {
                    onClose();
                    onOpenNote(r.capture_id);
                  }}
                  style={({ pressed }) => [styles.read, pressed && { opacity: 0.6 }]}
                  accessibilityRole="button"
                >
                  <View style={styles.readHead}>
                    <View style={[styles.stepDot, { backgroundColor: colour(r.valence) }]} />
                    <Text style={ui.meta}>
                      {formatStamp(r.created_at)} · {nameOf(r.valence).toLowerCase()}
                      {r.words.length ? `, ${r.words.join(", ")}` : ""}
                    </Text>
                  </View>
                  {r.evidence ? (
                    <Text style={styles.evidence}>“{r.evidence}”</Text>
                  ) : (
                    <Text style={ui.rowTitle} numberOfLines={2}>
                      {r.title}
                    </Text>
                  )}
                </Pressable>
              ))}
            </>
          )}
          {detail && reads.length === 0 && !shown?.yours && (
            <Text style={[ui.bodyMuted, { marginTop: 16 }]}>
              {"No note from this day said how you were. If you'd like, say it above."}
            </Text>
          )}
          <Text style={[ui.meta, { marginTop: 28 }]}>
            {"memo reads how your notes sound. It's a guess, and your word for a day always wins."}
          </Text>
        </ScrollView>
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((colors) => ({
  card: { backgroundColor: colors.surface, borderRadius: 20, paddingHorizontal: 16, paddingVertical: 16, marginTop: 12 },
  dots: { flexDirection: "row", justifyContent: "space-between", marginTop: 12 },
  dayCol: { alignItems: "center", gap: 6, minWidth: 32 },
  dot: { width: 18, height: 18, borderRadius: 9 },
  summary: { fontFamily: fonts.serif, fontSize: 17, lineHeight: 23, color: colors.text, marginTop: 12 },
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  heading: { fontFamily: fonts.serif, fontSize: 28, color: colors.text, marginTop: 4, marginBottom: 4 },
  section: { marginTop: 24, marginBottom: 10 },
  steps: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  step: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    minHeight: 44,
    paddingHorizontal: 14,
    borderRadius: 22,
    backgroundColor: colors.surfaceAlt,
  },
  stepDot: { width: 10, height: 10, borderRadius: 5 },
  stepText: { color: colors.text, fontSize: 15, fontWeight: "500" },
  link: { color: colors.record, fontSize: 15, fontWeight: "600" },
  read: { paddingVertical: 12, borderTopWidth: 1, borderTopColor: colors.hairline, gap: 6 },
  readHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  evidence: { fontFamily: fonts.serif, fontSize: 17, lineHeight: 23, color: colors.text },
}));
