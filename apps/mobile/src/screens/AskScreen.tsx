import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  Modal,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import EventSource from "react-native-sse";
import { Api, type Citation, type Message } from "../api";
import { IconCircle } from "../components";
import { formatStamp } from "../format";
import { useKeyboardHeight } from "../keyboard";
import { colors, ui } from "../ui";

type Turn = Message & { streaming?: boolean };
type StreamEvent = "retrieval" | "token" | "done" | "error";

// Multi-turn, streamed, citations arrive before the first token (issue #45). The
// sheet stays mounted so the conversation survives being closed and reopened.
export function AskSheet({
  api,
  visible,
  onClose,
  onOpenCapture,
}: {
  api: Api;
  visible: boolean;
  onClose: () => void;
  onOpenCapture: (id: string) => void;
}) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const source = useRef<EventSource<StreamEvent> | null>(null);
  const list = useRef<FlatList<Turn>>(null);
  const keyboard = useKeyboardHeight();

  useEffect(() => () => source.current?.close(), []);

  const reset = () => {
    source.current?.close();
    setConversationId(null);
    setTurns([]);
    setBusy(false);
  };

  const ensureConversation = useCallback(async () => {
    if (conversationId) return conversationId;
    const created = await api.createConversation();
    setConversationId(created.id);
    return created.id;
  }, [api, conversationId]);

  const patchTurn = (id: string, patch: (t: Turn) => Turn) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? patch(t) : t)));

  const ask = useCallback(async () => {
    const question = text.trim();
    if (!question || busy) return;
    setText("");
    setBusy(true);
    try {
      const cid = await ensureConversation();
      const accepted = await api.sendMessage(cid, question);
      setTurns((prev) => [
        ...prev,
        { id: `${accepted.message_id}-q`, role: "user", text: question, status: "done", error: null, citations: [] },
        { id: accepted.message_id, role: "assistant", text: "", status: "streaming", error: null, citations: [], streaming: true },
      ]);
      const es = new EventSource<StreamEvent>(api.streamUrl(accepted.stream_url), {
        headers: { Authorization: `Bearer ${api.token}` },
      });
      source.current = es;
      es.addEventListener("retrieval", (event) => {
        const data = JSON.parse(event.data ?? "{}") as { citations: Citation[] };
        patchTurn(accepted.message_id, (t) => ({ ...t, citations: data.citations }));
      });
      es.addEventListener("token", (event) => {
        const data = JSON.parse(event.data ?? "{}") as { delta: string };
        patchTurn(accepted.message_id, (t) => ({ ...t, text: (t.text ?? "") + data.delta }));
      });
      es.addEventListener("done", () => {
        patchTurn(accepted.message_id, (t) => ({ ...t, status: "done", streaming: false }));
        es.close();
        setBusy(false);
      });
      es.addEventListener("error", (event) => {
        const raw = "data" in event ? event.data : null;
        const detail = typeof raw === "string" ? JSON.parse(raw) : { message: "connection lost" };
        patchTurn(accepted.message_id, (t) => ({
          ...t,
          status: "failed",
          streaming: false,
          error: String((detail as { message?: string }).message ?? "failed"),
        }));
        es.close();
        setBusy(false);
      });
    } catch (error) {
      setBusy(false);
      setTurns((prev) => [
        ...prev,
        { id: `err-${Date.now()}`, role: "assistant", text: null, status: "failed", error: String(error), citations: [] },
      ]);
    }
  }, [api, busy, ensureConversation, text]);

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: keyboard }]}>
        <View style={styles.header}>
          <IconCircle name="add" onPress={reset} />
          <Text style={styles.headerTitle}>Ask</Text>
          <IconCircle name="close" onPress={onClose} />
        </View>
        <FlatList
          ref={list}
          data={turns}
          keyExtractor={(t) => t.id}
          contentContainerStyle={styles.list}
          onContentSizeChange={() => list.current?.scrollToEnd({ animated: true })}
          keyboardShouldPersistTaps="handled"
          renderItem={({ item }) =>
            item.role === "user" ? (
              <View style={styles.userBubble}>
                <Text style={styles.userText}>{item.text}</Text>
              </View>
            ) : (
              <AssistantTurn turn={item} onOpenCapture={onOpenCapture} />
            )
          }
          ListEmptyComponent={
            <View style={styles.emptyWrap}>
              <Text style={ui.empty}>Ask anything about your notes.</Text>
            </View>
          }
        />
        <View style={styles.composer}>
          <TextInput
            style={styles.input}
            placeholder="Message"
            placeholderTextColor={colors.muted}
            value={text}
            onChangeText={setText}
            onSubmitEditing={ask}
            returnKeyType="send"
          />
          <IconCircle name="arrow-up" tone="light" size={40} onPress={ask} />
        </View>
      </View>
    </Modal>
  );
}

// Retrieval returns the top-k candidates; the answer cites the ones it used as [n].
// Only those are sources. An answer that cites nothing has no sources.
function citedIn(text: string | null, citations: Citation[]): Citation[] {
  const ranks = new Set<number>();
  for (const m of (text ?? "").matchAll(/\[(\d+)\]/g)) ranks.add(Number(m[1]));
  return citations.filter((c) => ranks.has(c.rank));
}

function AssistantTurn({ turn, onOpenCapture }: { turn: Turn; onOpenCapture: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const cited = turn.streaming ? [] : citedIn(turn.text, turn.citations);
  return (
    <View style={styles.assistant}>
      <Text style={ui.body}>{turn.text || (turn.streaming ? "…" : "")}</Text>
      {turn.error && <Text style={[ui.danger, { marginTop: 8 }]}>{turn.error}</Text>}
      {cited.length > 0 && (
        <>
          <Pressable onPress={() => setOpen((o) => !o)} style={styles.sources}>
            <Text style={styles.sourcesText}>Sources</Text>
            <Ionicons name={open ? "chevron-up" : "chevron-down"} size={16} color={colors.text} />
          </Pressable>
          {open &&
            cited.map((c) => (
              <Pressable key={c.chunk_id} onPress={() => onOpenCapture(c.capture_id)} style={styles.citation}>
                <Text style={ui.meta}>
                  [{c.rank}] {formatStamp(c.created_at)}
                </Text>
                <Text style={styles.citationText} numberOfLines={3}>
                  {c.snippet}
                </Text>
              </Pressable>
            ))}
        </>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  sheet: { flex: 1, backgroundColor: colors.surface },
  header: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: 16,
    paddingTop: 16,
    paddingBottom: 12,
    backgroundColor: colors.surfaceAlt,
  },
  headerTitle: { color: colors.text, fontSize: 19, fontWeight: "700" },
  list: { padding: 16, paddingBottom: 24, flexGrow: 1 },
  emptyWrap: { flex: 1, justifyContent: "flex-end", paddingBottom: 24, paddingHorizontal: 24 },
  userBubble: {
    alignSelf: "flex-end",
    maxWidth: "85%",
    backgroundColor: colors.surfaceAlt,
    borderRadius: 22,
    paddingVertical: 12,
    paddingHorizontal: 18,
    marginBottom: 16,
  },
  userText: { color: colors.text, fontSize: 18, lineHeight: 24 },
  assistant: { marginBottom: 20 },
  sources: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    alignSelf: "flex-start",
    borderWidth: 1,
    borderColor: colors.faint,
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginTop: 14,
  },
  sourcesText: { color: colors.text, fontSize: 15, fontWeight: "700" },
  citation: {
    marginTop: 10,
    padding: 12,
    borderRadius: 14,
    backgroundColor: colors.surfaceAlt,
    gap: 4,
  },
  citationText: { color: colors.text, fontSize: 15, lineHeight: 21 },
  composer: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    paddingHorizontal: 16,
    paddingVertical: 12,
    paddingBottom: 28,
  },
  input: {
    flex: 1,
    color: colors.text,
    fontSize: 18,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 999,
    backgroundColor: colors.surfaceAlt,
  },
});
