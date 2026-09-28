import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, type Capture } from "../api";
import { AskBar, IconCircle, confirmDelete } from "../components";
import { ACTIVE_STATUSES, formatClock, formatStamp, headline, statusLabel } from "../format";
import { playUrl, stopPlayback, usePlayingId } from "../playback";
import { colors, ui } from "../ui";
import { TodaySection, useMorning } from "./TodaySection";

const POLL_MS = 3000;

export function HomeScreen({
  api,
  refreshKey,
  onOpen,
  onItems,
  onAsk,
  onRecord,
  onCompose,
  onSettings,
}: {
  api: Api;
  refreshKey: number;
  onOpen: (id: string) => void;
  onItems: () => void;
  onAsk: () => void;
  onRecord: () => void;
  onCompose: () => void;
  onSettings: () => void;
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
  const shown = q
    ? captures.filter((c) => (c.text ?? "").toLowerCase().includes(q))
    : captures;

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
        <IconCircle name="settings-outline" onPress={onSettings} />
        <View style={{ flex: 1 }} />
        <IconCircle name="checkbox-outline" onPress={onItems} />
      </View>
      <Text style={[ui.h1, ui.pad]}>memo</Text>
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
        <IconCircle name="create-outline" size={56} onPress={onCompose} />
        <Pressable
          onPress={onRecord}
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

const styles = StyleSheet.create({
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
    height: 50,
    marginBottom: 8,
  },
  searchInput: { flex: 1, color: colors.text, fontSize: 18 },
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
    height: 40,
  },
  chipDanger: { backgroundColor: "#2a1214" },
  chipText: { color: colors.text, fontSize: 16, fontWeight: "700" },
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
    borderColor: "#3a1a19",
  },
  recordPressed: { transform: [{ scale: 0.94 }] },
});
