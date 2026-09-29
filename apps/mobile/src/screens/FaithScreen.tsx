import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { Pressable, ScrollView, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  type Api,
  ApiError,
  type Capture,
  type Faith,
  type FaithMoment,
  type PassageRef,
  type Prayer,
  type ReadingState,
  type Update,
} from "../api";
import { asTranslation, openInBible, type Translation } from "../bible";
import { IconCircle, Pill } from "../components";
import { ask } from "../dialogs";
import { useRefetchOnFocus } from "../focus";
import { formatDay, formatStamp, headline, lookBack, toIsoDate } from "../format";
import { haptic } from "../haptics";
import { celebrate, PressableScale } from "../motion";
import { usePullToRefresh } from "../refresh";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";
import { PrayerSheet } from "./PrayerSheet";
import { UpdateCard } from "./UpdateCard";

// Faith (ADR 0042): a space of its own, turned on in Settings. One thing a day to
// remember, what you're reading (you set it; there is no fixed plan), the passages
// your notes talk about, prayers, and the notes written here. No score anywhere: an
// open prayer is never overdue, and nothing is behind. A later note that sounds like an
// answer is asked about at the top (ADR 0048): "Answered?", with the note's words.
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
  const [sheet, setSheet] = useState<{ existing?: Prayer } | null>(null);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<Update[]>([]);

  const load = useCallback(async () => {
    try {
      const [f, n, waiting] = await Promise.all([
        api.getFaith(),
        api.listCaptures(50, "faith"),
        // An older server has no /v1/updates: Faith still works without them.
        api.listUpdates({ status: "pending", kind: "prayer" }).catch(() => []),
      ]);
      setFaith(f);
      setNotes(n.items);
      setAsking(waiting);
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

  const translation = asTranslation(faith?.translation);

  // Every change to what you're reading comes back as the new state.
  const change = async (work: () => Promise<ReadingState>, done?: () => void) => {
    if (busy) return;
    setBusy(true);
    try {
      const reading = await work();
      done?.();
      setFaith((f) => (f ? { ...f, reading } : f));
      setError(null);
      onChanged();
    } catch (err) {
      setError(err instanceof ApiError && err.status === 422 ? "memo couldn't find that passage." : String(err));
    } finally {
      setBusy(false);
    }
  };

  // Typed, because a passage is quicker to type than to pick: "John", "Psalm 23".
  const choose = async () => {
    const ref = await ask({
      title: "What are you reading?",
      message: "A book, or a chapter or a few, like John, Psalms or Romans 8.",
      initial: faith?.reading?.current?.ref ?? "",
    });
    if (ref) void change(() => api.setReading(ref), haptic.tap);
  };
  const readOther = async () => {
    const ref = await ask({ title: "What did you read?", message: "Like Psalm 23, or John 3–4." });
    if (ref) void change(() => api.logReading(ref), haptic.done);
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
        refreshControl={pull}
      >
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}

        {asking.length > 0 && (
          <View style={{ marginTop: 12 }}>
            {asking.map((u) => (
              <UpdateCard
                key={u.id}
                update={u}
                onOpenNote={onOpenNote}
                onDecide={async (yes) => {
                  await api.answerUpdate(u.id, yes ? "yes" : "no");
                  await load();
                  if (yes) onChanged();
                }}
              />
            ))}
          </View>
        )}

        {faith?.moment && (
          <MomentCard
            moment={faith.moment}
            onOpenPrayer={(p) => setSheet({ existing: p })}
            onOpenNote={onOpenNote}
            onOpenPassage={(p) => openInBible(p, translation)}
          />
        )}

        {faith?.reading && (
          <ReadingCard
            reading={faith.reading}
            translation={translation}
            busy={busy}
            onRead={() => void change(() => api.logReading(), haptic.done)}
            onChoose={choose}
            onReadOther={readOther}
            onUndo={(id) => void change(() => api.undoReading(id), haptic.tap)}
          />
        )}

        {!!faith?.passages?.length && (
          <>
            <Text style={[ui.label, styles.section]}>Passages in your notes</Text>
            <View style={styles.card}>
              {faith.passages.map((p, i) => (
                <PassageRow
                  key={p.passage.ref}
                  entry={p}
                  first={i === 0}
                  onOpenNote={onOpenNote}
                  onOpenPassage={() => openInBible(p.passage, translation)}
                  translation={translation}
                />
              ))}
            </View>
          </>
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

// What you're reading, as you set it: the next chapter in it, a way to open it and a
// way to say it's read. Nothing is behind; changing it keeps everything you read.
function ReadingCard({
  reading,
  translation,
  busy,
  onRead,
  onChoose,
  onReadOther,
  onUndo,
}: {
  reading: ReadingState;
  translation: Translation;
  busy: boolean;
  onRead: () => void;
  onChoose: () => void;
  onReadOther: () => void;
  onUndo: (id: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const readButton = useRef<View>(null);
  const { current, next, last } = reading;
  // A few leaves from the button: the same small "done" as ticking a todo (ADR 0032).
  const read = () => {
    readButton.current?.measureInWindow((x, y, w, h) => celebrate(x + w / 2, y + h / 2));
    onRead();
  };
  const today = toIsoDate(new Date());
  return (
    <View style={[styles.card, styles.reading]}>
      {current ? (
        <>
          <Text style={ui.meta}>Reading · {current.ref}</Text>
          {next ? (
            <Text style={styles.readingLabel}>{next.ref}</Text>
          ) : (
            <Text style={[ui.bodyMuted, { marginTop: 6 }]}>{`You've read ${current.ref}. What's next?`}</Text>
          )}
          <View style={styles.readingActions}>
            {next ? (
              <>
                <Pill label={`Open in ${translation}`} icon="book-outline" onPress={() => openInBible(next, translation)} />
                <View ref={readButton} collapsable={false}>
                  <Pill label="Read" icon="checkmark" tone="light" busy={busy} onPress={read} />
                </View>
              </>
            ) : (
              <Pill label="Choose what's next" icon="book-outline" tone="light" onPress={onChoose} />
            )}
          </View>
        </>
      ) : (
        <>
          <Text style={ui.meta}>Reading</Text>
          <Text style={styles.readingPrompt}>What are you reading?</Text>
          <Text style={ui.bodyMuted}>
            A book or a few chapters, like John or Romans 8. memo offers the next chapter, and keeps what you read.
          </Text>
          <View style={styles.readingActions}>
            <Pill label="Choose" icon="book-outline" tone="light" onPress={onChoose} />
          </View>
        </>
      )}
      <View style={styles.readingLinks}>
        {current && (
          <Pressable onPress={onChoose} hitSlop={8} accessibilityRole="button">
            <Text style={styles.link}>Change</Text>
          </Pressable>
        )}
        <Pressable onPress={onReadOther} hitSlop={8} accessibilityRole="button">
          <Text style={styles.link}>I read something else</Text>
        </Pressable>
      </View>
      {last && (
        <Pressable onPress={() => onUndo(last.id)} hitSlop={8} style={styles.undo} accessibilityRole="button">
          <Text style={ui.meta}>
            Read {last.passage.ref} {last.on === today ? "today" : formatDay(last.on)} ·{" "}
            <Text style={styles.undoLink}>Undo</Text>
          </Text>
        </Pressable>
      )}
    </View>
  );
}

// A passage your notes talk about: how often and when, and, opened, the notes that
// name it and a link to read it.
function PassageRow({
  entry,
  first,
  onOpenNote,
  onOpenPassage,
  translation,
}: {
  entry: PassageRef;
  first: boolean;
  onOpenNote: (id: string) => void;
  onOpenPassage: () => void;
  translation: Translation;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const notes = entry.count === 1 ? "1 note" : `${entry.count} notes`;
  return (
    <View style={[styles.passage, !first && styles.rowRule]}>
      <Pressable
        onPress={() => {
          haptic.tap();
          setOpen((o) => !o);
        }}
        style={styles.passageHead}
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
      >
        <View style={{ flex: 1 }}>
          <Text style={styles.prayerTitle}>{entry.passage.ref}</Text>
          <Text style={ui.meta}>
            {notes} · last {formatDay(entry.last_on)}
          </Text>
        </View>
        <Ionicons name={open ? "chevron-up" : "chevron-down"} size={16} color={colors.faint} />
      </Pressable>
      {open && (
        <View style={styles.passageNotes}>
          {entry.notes.map((n) => (
            <Pressable key={n.capture_id} onPress={() => onOpenNote(n.capture_id)} accessibilityRole="button">
              <Text style={ui.meta}>{formatDay(n.on, true)}</Text>
              <Text style={ui.bodyMuted} numberOfLines={3}>
                {n.excerpt ?? "A note"}
              </Text>
            </Pressable>
          ))}
          <Pressable onPress={onOpenPassage} style={styles.refRow} accessibilityRole="link">
            <Text style={styles.link}>Open in {translation}</Text>
            <Ionicons name="open-outline" size={14} color={colors.record} />
          </Pressable>
        </View>
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
  readingPrompt: { fontFamily: fonts.serif, fontSize: 26, color: colors.text, marginTop: 6, marginBottom: 4 },
  readingLinks: { flexDirection: "row", gap: 18, marginTop: 14 },
  link: { color: colors.record, fontSize: 15, fontWeight: "600" },
  undo: { marginTop: 12 },
  undoLink: { color: colors.record, fontWeight: "600" },
  passage: { paddingVertical: 14 },
  passageHead: { flexDirection: "row", alignItems: "center", gap: 12 },
  passageNotes: { gap: 12, marginTop: 12 },
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
