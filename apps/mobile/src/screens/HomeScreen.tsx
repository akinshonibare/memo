import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActionSheetIOS,
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, type Capture } from "../api";
import { AskBar, Avatar, IconCircle, confirmDelete } from "../components";
import { ACTIVE_STATUSES, formatClock, formatStamp, headline, statusLabel } from "../format";
import { pickPhoto } from "../photo";
import { playUrl, stopPlayback, usePlayingId } from "../playback";
import { colors, ui } from "../ui";
import { TodaySection, useMorning } from "./TodaySection";
import { haptic } from "../haptics";

const POLL_MS = 3000;

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
}: {
  api: Api;
  refreshKey: number;
  onOpen: (id: string) => void;
  onPlanner: () => void;
  onReview: () => void;
  onAsk: () => void;
  onRecord: () => void;
  onCompose: () => void;
  onSettings: () => void;
  name: string | null;
}) {
  const insets = useSafeAreaInsets();
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);
  const morning = useMorning(api, refreshKey);

  const refresh = useCallback(async () => {
    try {
      const page = await api.listCaptures(50);
      setCaptures(page.items);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);

  const anyActive = captures.some((c) => ACTIVE_STATUSES.has(c.status));
  useEffect(() => {
    const first = setTimeout(refresh, 0);
    const timer = setInterval(refresh, anyActive ? POLL_MS : POLL_MS * 5);
    return () => {
      clearTimeout(first);
      clearInterval(timer);
    };
  }, [refresh, anyActive, refreshKey]);

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
  const shown = q && ranked ? ranked : typed;

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
  // its own big button because it is the one used without looking.
  const [uploading, setUploading] = useState(false);
  const addPhoto = async (source: "camera" | "library") => {
    try {
      const uri = await pickPhoto(source);
      if (!uri) return;
      setUploading(true);
      await api.createImageCapture(uri);
      haptic.done();
      await refresh();
    } catch (e) {
      setError(`Couldn't add the photo: ${String(e)}`);
    } finally {
      setUploading(false);
    }
  };
  const add = () => {
    const options = ["Write a note", "Take a photo", "Choose a photo", "Cancel"];
    const choose = (i: number) => {
      if (i === 0) onCompose();
      if (i === 1) addPhoto("camera");
      if (i === 2) addPhoto("library");
    };
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions({ options, cancelButtonIndex: 3 }, choose);
      return;
    }
    Alert.alert("Add", undefined, [
      ...options.slice(0, 3).map((text, i) => ({ text, onPress: () => choose(i) })),
      { text: "Cancel", style: "cancel" as const },
    ]);
  };

  const more = (c: Capture) =>
    confirmDelete("Delete this note? Its todos, events and ideas are kept.", async () => {
      if (playingId === c.id) stopPlayback();
      setCaptures((prev) => prev.filter((x) => x.id !== c.id));
      try {
        await api.deleteCapture(c.id);
      } catch (e) {
        setError(`Couldn't delete: ${String(e)}`);
        refresh();
      }
    });

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <Avatar name={name} size={44} onPress={onSettings} />
        <View style={{ flex: 1 }} />
        <IconCircle name="reader-outline" onPress={onReview} />
        <IconCircle name="calendar-outline" onPress={onPlanner} />
      </View>
      <Text style={[ui.h1, ui.pad, styles.wordmark]}>
        memo<Text style={{ color: colors.record }}>.</Text>
      </Text>
      <Text style={[ui.meta, ui.pad, styles.greeting]}>{greeting(name)}</Text>
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
      <FlatList
        data={shown}
        keyExtractor={(c) => c.id}
        renderItem={({ item }) => (
          <Row
            capture={item}
            playing={playingId === item.id}
            onPress={() => onOpen(item.id)}
            onPlay={() => togglePlay(item)}
            onMore={() => more(item)}
          />
        )}
        ItemSeparatorComponent={() => (
          <View style={[ui.hairline, { marginHorizontal: 20 }]} />
        )}
        contentContainerStyle={{ paddingBottom: 140 + insets.bottom }}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          morning && !q ? <TodaySection view={morning} onOpen={onOpen} /> : null
        }
        ListEmptyComponent={
          <Text style={[ui.empty, { marginTop: 48 }]}>
            {q
              ? "Nothing matches."
              : "Nothing captured yet.\nTap record, or write a note."}
          </Text>
        }
      />
      <View
        style={[styles.bottom, { paddingBottom: Math.max(insets.bottom, 16) }]}
      >
        <AskBar onPress={onAsk} />
        {uploading ? (
          <View style={styles.uploading}>
            <ActivityIndicator color={colors.text} />
          </View>
        ) : (
          <IconCircle name="add" size={56} onPress={add} />
        )}
        <Pressable
          onPress={() => {
            haptic.press();
            onRecord();
          }}
          style={({ pressed }) => [
            styles.record,
            pressed && styles.recordPressed,
          ]}
          accessibilityLabel="Record a voice note"
        />
      </View>
    </View>
  );
}

function Row({
  capture,
  playing,
  onPress,
  onPlay,
  onMore,
}: {
  capture: Capture;
  playing: boolean;
  onPress: () => void;
  onPlay: () => void;
  onMore: () => void;
}) {
  const { title, rest: snippet } = headline(capture);
  const status = statusLabel(capture.status);
  const failed = capture.status === "failed";
  const showActions = true;
  return (
    <Pressable
      onPress={onPress}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.7 }]}
    >
      <Text style={ui.meta}>{formatStamp(capture.created_at)}</Text>
      <Text style={ui.rowTitle} numberOfLines={2}>
        {title}
      </Text>
      {snippet && (
        <Text style={ui.bodyMuted} numberOfLines={2}>
          {snippet}
        </Text>
      )}
      {showActions && (
        <View style={styles.rowActions}>
          {capture.kind === "audio" && (
            <Pressable onPress={onPlay} style={({ pressed }) => [styles.chip, pressed && { opacity: 0.6 }]}>
              <Ionicons name={playing ? "stop" : "play"} size={16} color={colors.text} />
              <Text style={styles.chipText}>
                {capture.duration_ms != null ? formatClock(capture.duration_ms) : "Play"}
              </Text>
            </Pressable>
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
          <View style={{ flex: 1 }} />
          <IconCircle name="ellipsis-horizontal" size={40} onPress={onMore} />
        </View>
      )}
    </Pressable>
  );
}

// "Good evening, Akin · Monday 28 September": the first name only, and nothing at all
// in its place when no name is set.
function greeting(name: string | null): string {
  const now = new Date();
  const hour = now.getHours();
  const part = hour < 5 ? "evening" : hour < 12 ? "morning" : hour < 18 ? "afternoon" : "evening";
  const first = name?.trim().split(/\s+/)[0];
  const day = now.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
  return `Good ${part}${first ? `, ${first}` : ""} · ${day}`;
}

const styles = StyleSheet.create({
  wordmark: { marginBottom: 2 },
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
  row: { paddingHorizontal: 20, paddingVertical: 18, gap: 2 },
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
  bottom: {
    position: "absolute",
    left: 20,
    right: 20,
    bottom: 0,
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
  },
  record: {
    width: 64,
    height: 64,
    borderRadius: 32,
    backgroundColor: colors.record,
    borderWidth: 6,
    borderColor: "#4A3317",
  },
  recordPressed: { transform: [{ scale: 0.94 }] },
  uploading: {
    width: 56,
    height: 56,
    borderRadius: 28,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
});
