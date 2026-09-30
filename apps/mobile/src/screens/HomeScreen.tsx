import { Ionicons } from "@expo/vector-icons";
import { useIsFocused } from "@react-navigation/native";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AccessibilityInfo,
  Pressable,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from "react-native";
import ReanimatedSwipeable, { type SwipeableMethods } from "react-native-gesture-handler/ReanimatedSwipeable";
import Animated, { FadeOut, interpolate, type SharedValue, useAnimatedStyle } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, type Capture } from "../api";
import { AskBar, Avatar, IconCircle, confirmDelete } from "../components";
import { choose } from "../dialogs";
import { useNews } from "../news";
import { Places, PLACES_SIZE } from "./Places";
import { ACTIVE_STATUSES, formatClock, formatStamp, headline, statusLabel, toIsoDate } from "../format";
import { celebrate, InkText, PressableScale, rowEnter, Wordmark } from "../motion";
import { greeting, type Moment, momentText, particleForSearch } from "../delight";
import { pickPhoto } from "../photo";
import { playUrl, stopPlayback, usePlayingId } from "../playback";
import { makeStyles, useTheme } from "../theme";
import { useUi } from "../ui";
import { usePullToRefresh } from "../refresh";
import { OutboxSection } from "./OutboxSection";
import { TodaySection, useMorning } from "./TodaySection";
import { haptic } from "../haptics";
import { useOutbox } from "../outbox";

const POLL_MS = 3000;
// How long a deleted row takes to fade before it leaves the list.
const LEAVE_MS = 160;
// How tall the fade above the bottom controls is.
const BAR_FADE = 28;
const RECORD_SIZE = 64;
// "Not now" on a reach-out suggestion hides it for a week (PATCH snoozed_until).
const SNOOZE_DAYS = 7;

function inDays(n: number): Date {
  const d = new Date();
  d.setDate(d.getDate() + n);
  return d;
}

// What the list knows beyond the captures themselves: which rows are new since the
// last fetch (they slide in), and which just got their words (they ink in). Worked out
// when a fetch lands, by comparing with what was there before.
type ListState = { items: Capture[]; fresh: Set<string>; inked: Set<string>; loaded: boolean };
const EMPTY: ListState = { items: [], fresh: new Set(), inked: new Set(), loaded: false };

function nextList(prev: ListState, items: Capture[]): ListState {
  if (!prev.loaded) return { items, fresh: new Set(), inked: new Set(), loaded: true };
  const before = new Map(prev.items.map((c) => [c.id, c]));
  const fresh = new Set<string>();
  const inked = new Set(prev.inked);
  for (const c of items) {
    const old = before.get(c.id);
    if (!old) fresh.add(c.id);
    else if (!(old.text ?? "").trim() && (c.text ?? "").trim()) inked.add(c.id);
  }
  return { items, fresh, inked, loaded: true };
}

export function HomeScreen({
  api,
  refreshKey,
  onOpen,
  onPlanner,
  onReview,
  onAsk,
  onRecord,
  onCompose,
  onSettings,
  name,
  onPeople,
  onOpenPerson,
  onChanged,
  moment,
  onMomentSeen,
  onFaith,
  onHabits,
  onOpenHabit,
}: {
  api: Api;
  refreshKey: number;
  onOpen: (id: string) => void;
  // Faith (ADR 0042): its door on Home, only while it's turned on in Settings.
  onFaith?: () => void;
  // Habits (ADR 0043): the same, while Habits is on.
  onHabits: () => void;
  onOpenHabit?: (habitId: string) => void;
  onPlanner: () => void;
  onReview: () => void;
  onPeople: () => void;
  onOpenPerson: (id: string) => void;
  onChanged: () => void;
  onAsk: () => void;
  onRecord: () => void;
  onCompose: (photo?: string) => void; // with a photo already picked, to write beside it
  onSettings: () => void;
  name: string | null;
  moment: Moment | null;
  onMomentSeen: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const focused = useIsFocused();
  const [list, setList] = useState<ListState>(EMPTY);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  // Deleted here: it fades where it is (`leaving`), then leaves the list (`deleted`),
  // and stays gone from a refresh or search already on its way that still lists it.
  // Rows are never moved by a layout animation: in this virtualized list one cut short
  // (Home covered by a note, the app sent to the background) left every row below
  // where it was, and a note-sized gap on Home until something else moved.
  const [leaving, setLeaving] = useState<ReadonlySet<string>>(new Set());
  const [deleted, setDeleted] = useState<ReadonlySet<string>>(new Set());
  const captures = list.items.filter((c) => !deleted.has(c.id));
  const { view: morning, reload: reloadMorning } = useMorning(api, refreshKey);
  const fresh = useNews(api, refreshKey);

  const refresh = useCallback(async () => {
    try {
      const page = await api.listCaptures(50);
      setList((prev) => nextList(prev, page.items));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);

  // Polling only while Home is the screen in view: a note or the Planner pushed on top
  // keeps Home mounted underneath, and it has no reason to fetch for nobody.
  const anyActive = captures.some((c) => ACTIVE_STATUSES.has(c.status));
  useEffect(() => {
    if (!focused) return;
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, anyActive ? POLL_MS : POLL_MS * 5);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, anyActive, refreshKey, focused]);

  const pull = usePullToRefresh(
    useCallback(() => Promise.all([refresh(), reloadMorning()]), [refresh, reloadMorning]),
  );

  const waiting = useOutbox().some((i) => !i.failed);

  const q = query.trim().toLowerCase();
  const typed = q
    ? captures.filter((c) => (c.text ?? "").toLowerCase().includes(q))
    : captures;

  // Search by meaning (ADR 0028): instant on-device filtering while typing, then the
  // server's ranked results after a pause. A slower, older response never overwrites
  // a newer one.
  const [ranked, setRanked] = useState<Capture[] | null>(null);
  const latest = useRef("");
  useEffect(() => {
    const term = query.trim();
    latest.current = term;
    const timer = setTimeout(
      async () => {
        if (term.length < 2) {
          setRanked(null);
          return;
        }
        try {
          const hits = await api.search(term);
          if (latest.current !== term) return;
          setRanked(hits.flatMap((h) => (h.capture ? [h.capture] : [])));
        } catch {
          if (latest.current === term) setRanked(null); // keep the typed filter
        }
      },
      term.length < 2 ? 0 : 350,
    );
    return () => clearTimeout(timer);
  }, [query, api]);
  const shown = q && ranked ? ranked.filter((c) => !deleted.has(c.id)) : typed;

  // Typing "fireflies" (or snow, leaves, petals) into search lets them loose (ADR 0041).
  const { width, height } = useWindowDimensions();
  const particle = particleForSearch(query);
  const loosed = useRef<string | null>(null);
  useEffect(() => {
    if (particle && loosed.current !== particle) celebrate(width / 2, height * 0.35, true, particle);
    loosed.current = particle;
  }, [particle, width, height]);

  const playingId = usePlayingId();
  const togglePlay = async (c: Capture) => {
    if (playingId === c.id) {
      stopPlayback();
      return;
    }
    try {
      const audio = await api.getAudio(c.id);
      await playUrl(c.id, audio.url);
    } catch (e) {
      setError(`Couldn't play: ${String(e)}`);
    }
  };
  // "+" holds the quieter ways to capture: writing, and photos (ADR 0024). Record keeps
  // its own big button because it is the one used without looking. A photo opens the
  // note sheet with it attached, so you can say why you kept it (ADR 0056).
  const addPhoto = async (source: "camera" | "library") => {
    try {
      const uri = await pickPhoto(source);
      if (uri) onCompose(uri);
    } catch (e) {
      setError(`Couldn't add the photo: ${String(e)}`);
    }
  };
  const add = async () => {
    const picked = await choose({
      options: [
        { label: "Write a note", icon: "create-outline" },
        { label: "Take a photo", icon: "camera-outline" },
        { label: "Choose a photo", icon: "images-outline" },
      ],
    });
    if (picked === 0) onCompose();
    if (picked === 1) void addPhoto("camera");
    if (picked === 2) void addPhoto("library");
  };

  // Swipe left on a row, long-press it, or use the VoiceOver action: all three ask
  // the same question before anything is deleted.
  const remove = (c: Capture, onCancel?: () => void) =>
    confirmDelete(
      "Delete this note and everything memo took from it?",
      async () => {
        if (playingId === c.id) stopPlayback();
        setLeaving((prev) => new Set(prev).add(c.id));
        setTimeout(() => setDeleted((prev) => new Set(prev).add(c.id)), LEAVE_MS);
        try {
          await api.deleteCapture(c.id);
        } catch (e) {
          const without = (prev: ReadonlySet<string>) => {
            const next = new Set(prev);
            next.delete(c.id);
            return next;
          };
          setLeaving(without);
          setDeleted(without);
          setError(`Couldn't delete: ${String(e)}`);
          refresh();
        }
      },
      onCancel,
    );

  // New rows fade in, except while searching, where results replace each other on every
  // keystroke and movement would only be noise.
  const animateRows = !q;
  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <Avatar name={name} size={44} onPress={onSettings} />
      </View>
      <Wordmark live={anyActive || waiting} glow={!!moment} playful style={[ui.pad, styles.wordmark]} />
      <Text style={[ui.meta, ui.pad, styles.greeting]}>{greeting(name)}</Text>
      {moment && focused && <MomentBanner moment={moment} onDone={onMomentSeen} />}
      <View style={[styles.search, { marginHorizontal: 20 }]}>
        <Ionicons name="search" size={20} color={colors.muted} />
        <TextInput
          style={styles.searchInput}
          placeholder="Search"
          placeholderTextColor={colors.muted}
          value={query}
          onChangeText={setQuery}
          autoCapitalize="none"
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
      </View>
      {error && (
        <Text style={[ui.danger, ui.pad, { marginBottom: 8 }]}>{error}</Text>
      )}
      <Animated.FlatList
        data={shown}
        keyExtractor={(c) => c.id}
        refreshControl={pull}
        renderItem={({ item }) => (
          <Animated.View
            entering={animateRows && list.fresh.has(item.id) ? rowEnter : undefined}
            style={[styles.fade, leaving.has(item.id) && styles.gone]}
          >
            <SwipeRow
              capture={item}
              inked={list.inked.has(item.id)}
              playing={playingId === item.id}
              onPress={() => onOpen(item.id)}
              onPlay={() => togglePlay(item)}
              onDelete={(cancel) => remove(item, cancel)}
            />
          </Animated.View>
        )}
        ItemSeparatorComponent={() => (
          <View style={[ui.hairline, { marginHorizontal: 20 }]} />
        )}
        contentContainerStyle={{ paddingBottom: 140 + PLACES_SIZE + 12 + insets.bottom }}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          q ? null : (
            <>
              <OutboxSection />
              {morning && (
                <TodaySection
                  view={morning}
                  onOpen={onOpen}
                  onOpenPerson={onOpenPerson}
                  onTick={async (id) => {
                    await api.reviewDerivation(id, { completed: true });
                    onChanged();
                  }}
                  onSpoke={async (id) => {
                    await api.logContact(id);
                    onChanged();
                  }}
                  onNotNow={async (id) => {
                    await api.updatePerson(id, { snoozed_until: toIsoDate(inDays(SNOOZE_DAYS)) });
                    onChanged();
                  }}
                  onTrackHabit={async (p) => {
                    await api.trackHabit({ name: p.name, key: p.key });
                    onChanged();
                  }}
                  onDismissHabit={async (key) => {
                    await api.dismissHabit(key);
                    onChanged();
                  }}
                  onLogHabit={async (id) => {
                    await api.logHabit(id);
                    onChanged();
                  }}
                  onOpenHabits={onHabits}
                  onOpenHabit={onOpenHabit}
                />
              )}
              {shown.length > 0 && (
                <Text style={[ui.label, styles.notesLabel]} accessibilityRole="header">
                  Notes
                </Text>
              )}
            </>
          )
        }
        ListEmptyComponent={
          list.loaded || q ? (
            <Text style={[ui.empty, { marginTop: 48 }]}>
              {particle
                ? "You found them."
                : q
                  ? "Nothing matches."
                  : "Nothing captured yet.\nTap record, or write a note."}
            </Text>
          ) : null
        }
      />
      {/* The controls sit on the page rather than over the notes: the list fades out
          above them, and nothing shows between the buttons. The fade lets touches
          through to the list; the buttons' row does not. */}
      <View
        pointerEvents="box-none"
        style={[
          styles.bottomWrap,
          {
            experimental_backgroundImage: `linear-gradient(to bottom, ${colors.bg}00 0px, ${colors.bg} ${BAR_FADE}px, ${colors.bg} 100%)`,
          },
        ]}
      >
        <View style={[styles.bottom, { paddingBottom: Math.max(insets.bottom, 16) }]}>
        <AskBar onPress={onAsk} />
        <IconCircle name="add" size={56} onPress={() => void add()} accessibilityLabel="Write a note or add a photo" />
        <PressableScale
          onPress={() => {
            haptic.press();
            onRecord();
          }}
          scaleTo={0.9}
          style={styles.record}
          accessibilityRole="button"
          accessibilityLabel="Record a voice note"
        />
        </View>
      </View>
      <Places
        right={20 + (RECORD_SIZE - PLACES_SIZE) / 2}
        bottom={Math.max(insets.bottom, 16) + 6 + RECORD_SIZE + 12}
        places={[
          { key: "planner", icon: "calendar-outline", label: "Planner", onPress: onPlanner, fresh: fresh.has("planner") },
          { key: "review", icon: "reader-outline", label: "Review", onPress: onReview, fresh: fresh.has("review") },
          { key: "people", icon: "people-outline", label: "People", onPress: onPeople, fresh: fresh.has("people") },
          { key: "habits", icon: "leaf-outline", label: "Habits", onPress: onHabits, fresh: fresh.has("habits") },
          ...(onFaith
            ? [{ key: "faith", icon: "book-outline" as const, label: "Within", onPress: onFaith, fresh: fresh.has("faith") }]
            : []),
        ]}
      />
    </View>
  );
}

// A row that swipes left to reveal Delete. The "…" button it replaces only ever held
// Delete, so it's gone; a long press and the VoiceOver action reach the same place.
function SwipeRow({
  capture,
  inked,
  playing,
  onPress,
  onPlay,
  onDelete,
}: {
  capture: Capture;
  inked: boolean;
  playing: boolean;
  onPress: () => void;
  onPlay: () => void;
  onDelete: (onCancel: () => void) => void;
}) {
  const styles = useStyles();
  const swipe = useRef<SwipeableMethods>(null);
  const del = () => {
    haptic.tap();
    onDelete(() => swipe.current?.close());
  };
  return (
    <ReanimatedSwipeable
      ref={swipe}
      friction={1.6}
      rightThreshold={48}
      overshootRight={false}
      renderRightActions={(progress) => <DeleteAction progress={progress} onPress={del} />}
      containerStyle={styles.swipe}
    >
      <Row capture={capture} inked={inked} playing={playing} onPress={onPress} onPlay={onPlay} onDelete={del} />
    </ReanimatedSwipeable>
  );
}

function DeleteAction({ progress, onPress }: { progress: SharedValue<number>; onPress: () => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const animated = useAnimatedStyle(() => ({
    opacity: interpolate(progress.value, [0, 0.6, 1], [0, 0.6, 1]),
    transform: [{ scale: interpolate(progress.value, [0, 1], [0.7, 1], "clamp") }],
  }));
  return (
    <View style={styles.actionWrap}>
      <Pressable onPress={onPress} accessibilityRole="button" accessibilityLabel="Delete note" style={styles.action}>
        <Animated.View style={[styles.actionInner, animated]}>
          <Ionicons name="trash-outline" size={22} color={colors.danger} />
          <Text style={styles.actionText}>Delete</Text>
        </Animated.View>
      </Pressable>
    </View>
  );
}

function Row({
  capture,
  inked,
  playing,
  onPress,
  onPlay,
  onDelete,
}: {
  capture: Capture;
  inked: boolean;
  playing: boolean;
  onPress: () => void;
  onPlay: () => void;
  onDelete: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const { title, rest: snippet } = headline(capture);
  const status = statusLabel(capture.status);
  const failed = capture.status === "failed";
  return (
    <Pressable
      onPress={onPress}
      onLongPress={onDelete}
      delayLongPress={450}
      accessibilityActions={[{ name: "delete", label: "Delete note" }]}
      onAccessibilityAction={(e) => e.nativeEvent.actionName === "delete" && onDelete()}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
    >
      <Text style={ui.meta}>{formatStamp(capture.created_at)}</Text>
      <InkText
        key={inked ? "ink" : "plain"}
        text={title}
        play={inked}
        once={`row:${capture.id}`}
        style={ui.rowTitle}
        numberOfLines={2}
      />
      {snippet && (
        <Text style={ui.bodyMuted} numberOfLines={2}>
          {snippet}
        </Text>
      )}
      {(capture.kind !== "text" || status) && (
        <View style={styles.rowActions}>
          {capture.kind === "audio" && (
            <PressableScale onPress={onPlay} style={styles.chip} pressedStyle={{ opacity: 0.6 }} accessibilityRole="button">
              <Ionicons name={playing ? "stop" : "play"} size={16} color={colors.text} />
              <Text style={styles.chipText}>
                {capture.duration_ms != null ? formatClock(capture.duration_ms) : "Play"}
              </Text>
            </PressableScale>
          )}
          {capture.kind === "image" && (
            <View style={styles.chip}>
              <Ionicons name="image-outline" size={15} color={colors.text} />
              <Text style={styles.chipText}>Photo</Text>
            </View>
          )}
          {status && (
            <View style={[styles.chip, failed && styles.chipDanger]}>
              <Text
                style={[styles.chipText, failed && { color: colors.danger }]}
              >
                {status}
              </Text>
            </View>
          )}
        </View>
      )}
    </Pressable>
  );
}

// A milestone or memo's birthday (ADR 0041): a card that arrives with the season's
// particles and the wordmark's halo, is read out to VoiceOver, and goes by itself.
function MomentBanner({ moment, onDone }: { moment: Moment; onDone: () => void }) {
  const styles = useStyles();
  const ui = useUi();
  const card = useRef<View>(null);
  const { title, body } = momentText(moment);
  const done = useRef(onDone);
  useEffect(() => {
    done.current = onDone;
  }, [onDone]);
  useEffect(() => {
    haptic.done();
    AccessibilityInfo.announceForAccessibility(`${title} ${body}`);
    const burst = setTimeout(
      () => card.current?.measureInWindow((x, y, w, h) => celebrate(x + w / 2, y + h / 2, true)),
      350,
    );
    const away = setTimeout(() => done.current(), 9000);
    return () => {
      clearTimeout(burst);
      clearTimeout(away);
    };
  }, [title, body]);
  return (
    <Animated.View entering={rowEnter} exiting={FadeOut.duration(120)} style={[ui.pad, { marginBottom: 12 }]}>
      <Pressable
        ref={card}
        onPress={onDone}
        accessibilityRole="button"
        accessibilityHint="Dismisses"
        style={styles.moment}
      >
        <Text style={styles.momentTitle}>{title}</Text>
        <Text style={ui.bodyMuted}>{body}</Text>
      </Pressable>
    </Animated.View>
  );
}

const useStyles = makeStyles((colors) => ({
  fade: { opacity: 1, transitionProperty: "opacity", transitionDuration: LEAVE_MS },
  gone: { opacity: 0 },
  // Where Today ends and the notes begin; the same label style as Today's date.
  notesLabel: { paddingHorizontal: 20, paddingTop: 16 },
  wordmark: { marginTop: 12, marginBottom: 2 },
  moment: {
    backgroundColor: colors.surface,
    borderRadius: 20,
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderWidth: 1,
    borderColor: colors.record,
  },
  momentTitle: { fontFamily: "InstrumentSerif_400Regular", fontSize: 26, lineHeight: 30, color: colors.text },
  greeting: { marginBottom: 14 },
  topBar: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingTop: 4,
  },
  search: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    backgroundColor: colors.surface,
    borderRadius: 18,
    paddingHorizontal: 16,
    height: 44,
    marginBottom: 8,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 16 },
  swipe: { backgroundColor: colors.bg },
  row: { paddingHorizontal: 20, paddingVertical: 18, gap: 2, backgroundColor: colors.bg },
  rowActions: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginTop: 12,
  },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingHorizontal: 14,
    height: 34,
  },
  chipDanger: { backgroundColor: colors.dangerSurface },
  chipText: { color: colors.text, fontSize: 14, fontWeight: "600" },
  actionWrap: { width: 96, justifyContent: "center", paddingRight: 12 },
  action: {
    flex: 1,
    marginVertical: 10,
    borderRadius: 20,
    backgroundColor: colors.dangerSurface,
    alignItems: "center",
    justifyContent: "center",
  },
  actionInner: { alignItems: "center", gap: 4 },
  actionText: { color: colors.danger, fontSize: 13, fontWeight: "600" },
  bottomWrap: { position: "absolute", left: 0, right: 0, bottom: 0, paddingTop: BAR_FADE },
  bottom: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    paddingHorizontal: 20,
    paddingTop: 6,
  },
  record: {
    width: RECORD_SIZE,
    height: RECORD_SIZE,
    borderRadius: RECORD_SIZE / 2,
    backgroundColor: colors.record,
    borderWidth: 6,
    borderColor: colors.recordRing,
  },
}));
