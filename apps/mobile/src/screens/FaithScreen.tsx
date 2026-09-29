import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Capture, Faith, FaithMoment, Plan, Prayer } from "../api";
import { asTranslation, openInBible } from "../bible";
import { IconCircle, Pill } from "../components";
import { useRefetchOnFocus } from "../focus";
import { formatDay, formatStamp, headline, lookBack, toIsoDate } from "../format";
import { haptic } from "../haptics";
import { celebrate, PressableScale } from "../motion";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";
import { PrayerSheet } from "./PrayerSheet";

// Faith (ADR 0042): a space of its own, turned on in Settings. One thing a day to
// remember, the reading plan, prayers, and the notes written here. No score anywhere:
// the plan keeps time by position, and an open prayer is never overdue.
export function FaithScreen({
  api,
  refreshKey,
  onBack,
  onOpenNote,
  onWrite,
  onRecord,
  onChanged,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onOpenNote: (captureId: string) => void;
  onWrite: () => void;
  onRecord: () => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [faith, setFaith] = useState<Faith | null>(null);
  const [notes, setNotes] = useState<Capture[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [sheet, setSheet] = useState<{ existing?: Prayer } | null>(null);
  const [reading, setReading] = useState(false);

  const load = useCallback(async () => {
    try {
      const [f, n] = await Promise.all([api.getFaith(), api.listCaptures(50, "faith")]);
      setFaith(f);
      setNotes(n.items);
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

  const refresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const translation = asTranslation(faith?.translation);

  const markRead = async () => {
    if (reading) return;
    setReading(true);
    haptic.done();
    try {
      const plan = await api.readNext();
      setFaith((f) => (f ? { ...f, plan } : f));
      onChanged();
    } catch (err) {
      setError(String(err));
    } finally {
      setReading(false);
    }
  };

  const undo = async () => {
    try {
      const plan = await api.undoReading();
      haptic.tap();
      setFaith((f) => (f ? { ...f, plan } : f));
      onChanged();
    } catch (err) {
      setError(String(err));
    }
  };

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title}>Faith</Text>
        <View style={{ flex: 1 }} />
        <IconCircle
          name="add"
          size={38}
          tone="light"
          onPress={() => setSheet({})}
          accessibilityLabel="New prayer"
        />
      </View>

      <ScrollView
        contentContainerStyle={[ui.pad, { paddingBottom: 140 + insets.bottom }]}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.muted} />}
      >
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}

        {faith?.moment && (
          <MomentCard
            moment={faith.moment}
            onOpenPrayer={(p) => setSheet({ existing: p })}
            onOpenNote={onOpenNote}
            onOpenPassage={(p) => openInBible(p, translation)}
          />
        )}

        {faith && (
          <ReadingCard
            plan={faith.plan}
            translation={translation}
            busy={reading}
            onRead={markRead}
            onUndo={undo}
          />
        )}

        {faith && (
          <>
            <Text style={[ui.label, styles.section]}>Praying for</Text>
            {faith.open.length === 0 ? (
              <Pressable onPress={() => setSheet({})} style={styles.emptyRow} accessibilityRole="button">
                <Ionicons name="add-circle-outline" size={20} color={colors.muted} />
                <Text style={ui.bodyMuted}>{"Add something you're praying for"}</Text>
              </Pressable>
            ) : (
              <View style={styles.card}>
                {faith.open.map((p, i) => (
                  <PrayerRow key={p.id} prayer={p} first={i === 0} onPress={() => setSheet({ existing: p })} />
                ))}
              </View>
            )}

            {faith.answered.length > 0 && (
              <>
                <Text style={[ui.label, styles.section]}>Answered</Text>
                <View style={styles.card}>
                  {faith.answered.map((p, i) => (
                    <PrayerRow key={p.id} prayer={p} first={i === 0} onPress={() => setSheet({ existing: p })} />
                  ))}
                </View>
              </>
            )}
          </>
        )}

        <Text style={[ui.label, styles.section]}>Notes</Text>
        {notes.length === 0 ? (
          <Text style={[ui.bodyMuted, styles.hint]}>
            {
              "Write or record a reflection after you read. Name the passage, like Romans 8:28, and memo keeps it for you."
            }
          </Text>
        ) : (
          notes.map((c) => <NoteRow key={c.id} capture={c} onPress={() => onOpenNote(c.id)} />)
        )}
      </ScrollView>

      <View style={[styles.bottom, { paddingBottom: Math.max(insets.bottom, 16) }]}>
        <Pill label="Write" icon="create-outline" onPress={onWrite} />
        <PressableScale
          onPress={() => {
            haptic.press();
            onRecord();
          }}
          scaleTo={0.9}
          style={styles.record}
          accessibilityRole="button"
          accessibilityLabel="Record a reflection"
        />
      </View>

      {sheet && (
        <PrayerSheet
          api={api}
          existing={sheet.existing}
          onClose={() => setSheet(null)}
          onSaved={() => {
            void load();
            onChanged();
          }}
          onOpenNote={onOpenNote}
        />
      )}
    </View>
  );
}

// The one thing a day: a prayer from this date in another year, or a passage you wrote
// about, with what you wrote.
function MomentCard({
  moment,
  onOpenPrayer,
  onOpenNote,
  onOpenPassage,
}: {
  moment: FaithMoment;
  onOpenPrayer: (p: Prayer) => void;
  onOpenNote: (id: string) => void;
  onOpenPassage: (p: NonNullable<FaithMoment["passage"]>) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const today = toIsoDate(new Date());
  if (moment.kind === "prayer" && moment.prayer) {
    const p = moment.prayer;
    return (
      <Pressable onPress={() => onOpenPrayer(p)} style={[styles.card, styles.moment]} accessibilityRole="button">
        <Text style={ui.meta}>{lookBack(moment.on, today)} you prayed</Text>
        <Text style={styles.momentTitle}>{p.title}</Text>
        {p.answered_at ? (
          <Text style={styles.answered}>
            Answered {formatDay(p.answered_at.slice(0, 10), true)}
            {p.answer ? `. ${p.answer}` : ""}
          </Text>
        ) : (
          <Text style={ui.bodyMuted}>Still praying, or has it been answered?</Text>
        )}
      </Pressable>
    );
  }
  if (moment.kind === "passage" && moment.passage) {
    const passage = moment.passage;
    return (
      <View style={[styles.card, styles.moment]}>
        <Text style={ui.meta}>From your notes · {formatDay(moment.on, true)}</Text>
        <Pressable onPress={() => onOpenPassage(passage)} accessibilityRole="link" style={styles.refRow}>
          <Text style={styles.momentTitle}>{passage.ref}</Text>
          <Ionicons name="open-outline" size={16} color={colors.muted} />
        </Pressable>
        {moment.excerpt && moment.capture_id && (
          <Pressable onPress={() => onOpenNote(moment.capture_id!)} accessibilityRole="button">
            <Text style={ui.bodyMuted} numberOfLines={4}>
              {moment.excerpt}
            </Text>
          </Pressable>
        )}
      </View>
    );
  }
  return null;
}

// Where you are, today's reading, a way to open it and a way to say it's read. Never
// how far behind: a week away is a week paused.
function ReadingCard({
  plan,
  translation,
  busy,
  onRead,
  onUndo,
}: {
  plan: Plan;
  translation: string;
  busy: boolean;
  onRead: () => void;
  onUndo: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const readButton = useRef<View>(null);
  const finishedOnce = plan.cycle > 1 && plan.position === 0 && plan.last;
  // A few leaves from the button: the same small "done" as ticking a todo (ADR 0032).
  const read = () => {
    readButton.current?.measureInWindow((x, y, w, h) => celebrate(x + w / 2, y + h / 2));
    onRead();
  };
  return (
    <View style={[styles.card, styles.reading]}>
      <Text style={ui.meta}>
        Reading · {plan.title} · {plan.position + 1} of {plan.total}
      </Text>
      {finishedOnce && (
        <Text style={[ui.bodyMuted, { marginTop: 6 }]}>
          {"You've read the whole Bible. Starting again at Genesis."}
        </Text>
      )}
      {plan.next && <Text style={styles.readingLabel}>{plan.next.label}</Text>}
      {plan.next && (
        <View style={styles.readingActions}>
          <Pill
            label={`Open in ${translation}`}
            icon="book-outline"
            onPress={() => openInBible(plan.next!, translation as never)}
          />
          <View ref={readButton} collapsable={false}>
            <Pill label="Read" icon="checkmark" tone="light" busy={busy} onPress={read} />
          </View>
        </View>
      )}
      {plan.last && !finishedOnce && (
        <Pressable onPress={onUndo} hitSlop={8} style={styles.undo} accessibilityRole="button">
          <Text style={ui.meta}>
            Read {plan.last.label} · <Text style={styles.undoLink}>Undo</Text>
          </Text>
        </Pressable>
      )}
    </View>
  );
}

function PrayerRow({ prayer, first, onPress }: { prayer: Prayer; first: boolean; onPress: () => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const meta = prayer.answered_at
    ? [`Answered ${formatDay(prayer.answered_at.slice(0, 10))}`, prayer.answer].filter(Boolean).join(" · ")
    : [prayer.person_name, prayer.notes].filter(Boolean).join(" · ");
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.prayer, !first && styles.rowRule, pressed && { opacity: 0.6 }]}
      accessibilityRole="button"
    >
      <Ionicons
        name={prayer.answered_at ? "checkmark-circle" : "ellipse-outline"}
        size={18}
        color={prayer.answered_at ? colors.forest : colors.faint}
        style={{ marginTop: 2 }}
      />
      <View style={{ flex: 1 }}>
        <Text style={styles.prayerTitle}>{prayer.title}</Text>
        {!!meta && (
          <Text style={ui.meta} numberOfLines={2}>
            {meta}
          </Text>
        )}
      </View>
    </Pressable>
  );
}

function NoteRow({ capture, onPress }: { capture: Capture; onPress: () => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { title } = headline(capture);
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.note, pressed && { opacity: 0.6 }]}>
      <Text style={ui.meta}>{formatStamp(capture.created_at)}</Text>
      <Text style={ui.rowTitle} numberOfLines={2}>
        {title}
      </Text>
    </Pressable>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, paddingBottom: 8 },
  title: { fontFamily: fonts.serif, fontSize: 30, color: colors.text, marginLeft: 4 },
  section: { marginTop: 24, marginBottom: 8 },
  card: { backgroundColor: colors.surface, borderRadius: 20, paddingHorizontal: 16, marginTop: 12 },
  moment: { paddingVertical: 16, gap: 4 },
  momentTitle: { color: colors.text, fontSize: 19, fontWeight: "600" },
  answered: { color: colors.text, fontSize: 15, lineHeight: 21, marginTop: 2 },
  refRow: { flexDirection: "row", alignItems: "center", gap: 6 },
  reading: { paddingVertical: 16 },
  readingLabel: { fontFamily: fonts.serif, fontSize: 30, color: colors.text, marginTop: 6 },
  readingActions: { flexDirection: "row", gap: 10, marginTop: 14 },
  undo: { marginTop: 12 },
  undoLink: { color: colors.record, fontWeight: "600" },
  prayer: { flexDirection: "row", gap: 12, paddingVertical: 14 },
  rowRule: { borderTopWidth: 1, borderTopColor: colors.hairline },
  prayerTitle: { color: colors.text, fontSize: 16, fontWeight: "500" },
  emptyRow: { flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 8 },
  hint: { lineHeight: 22 },
  note: { paddingVertical: 14, borderBottomWidth: 1, borderBottomColor: colors.hairline },
  bottom: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0,
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "flex-end",
    gap: 14,
    paddingHorizontal: 20,
    paddingTop: 12,
    backgroundColor: colors.bg,
  },
  record: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.record,
    borderWidth: 6,
    borderColor: colors.recordRing,
  },
}));
