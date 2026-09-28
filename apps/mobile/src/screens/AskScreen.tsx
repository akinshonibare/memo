import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import EventSource from "react-native-sse";
import { Api, type Citation, type Message } from "../api";
import { colors, ui } from "../ui";

type Turn = Message & { streaming?: boolean };
type StreamEvent = "retrieval" | "token" | "done" | "error";

// Multi-turn, streamed, citations visible before the first token (issue #45).
export function AskScreen({ api, onOpenCapture }: { api: Api; onOpenCapture: (id: string) => void }) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const source = useRef<EventSource<StreamEvent> | null>(null);

  useEffect(() => () => source.current?.close(), []);

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
    <View style={ui.screen}>
      <FlatList
        data={turns}
        keyExtractor={(t) => t.id}
        contentContainerStyle={{ paddingBottom: 12 }}
        renderItem={({ item }) => (
          <View style={[ui.card, item.role === "user" && styles.user]}>
            {item.role === "assistant" && item.citations.length > 0 && (
              <View style={styles.citations}>
                {item.citations.map((c) => (
                  <Pressable key={c.chunk_id} onPress={() => onOpenCapture(c.capture_id)} style={styles.citation}>
                    <Text style={styles.citationText} numberOfLines={2}>
                      [{c.rank}] {c.snippet}
                    </Text>
                    <Text style={ui.cardMeta}>{c.created_at.slice(0, 10)}</Text>
                  </Pressable>
                ))}
              </View>
            )}
            <Text style={ui.cardBody}>
              {item.text || (item.streaming ? "…" : "")}
            </Text>
            {item.error && <Text style={styles.failure}>{item.error}</Text>}
          </View>
        )}
        ListEmptyComponent={<Text style={ui.empty}>Ask anything you've written down.</Text>}
      />
      <View style={styles.composer}>
        <TextInput
          style={styles.input}
          placeholder="What did I decide about…"
          placeholderTextColor={colors.muted}
          value={text}
          onChangeText={setText}
          onSubmitEditing={ask}
          returnKeyType="send"
        />
        <Pressable style={[ui.button, (busy || !text.trim()) && ui.buttonDisabled]} onPress={ask} disabled={busy}>
          <Text style={ui.buttonText}>Ask</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  user: { backgroundColor: colors.accentSoft, alignSelf: "flex-end", maxWidth: "85%" },
  citations: { marginBottom: 8, gap: 6 },
  citation: { padding: 8, borderRadius: 8, backgroundColor: colors.surfaceAlt },
  citationText: { color: colors.text, fontSize: 13 },
  composer: { flexDirection: "row", gap: 8, alignItems: "center" },
  input: {
    flex: 1,
    fontSize: 16,
    color: colors.text,
    padding: 12,
    borderRadius: 12,
    backgroundColor: colors.surface,
  },
  failure: { color: colors.danger, marginTop: 6 },
});
