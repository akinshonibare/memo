import { Ionicons } from "@expo/vector-icons";
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder } from "expo-audio";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
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
import { useLiveVoice } from "../liveVoice";
import { SentenceSplitter, SpeechQueue } from "../voice";
import { haptic } from "../haptics";

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

  // Voice mode (ADR 0025, step 1): hold the mic to ask; an answer to a spoken question
  // is read aloud a sentence at a time as it streams.
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const [voice, setVoice] = useState<"idle" | "listening" | "transcribing">("idle");
  const [speaking, setSpeaking] = useState(false);
  const [hint, setHint] = useState<string | null>(null);
  const pressedAt = useRef(0);
  // Set only once recording really started: the permission prompt or a failure can
  // leave the finger lifted before it does.
  const recording = useRef(false);
  const released = useRef(false);
  // The one answer currently allowed to speak. Clearing it silences the rest of that
  // answer's sentences even though its text keeps streaming onto the screen.
  const speakingTurn = useRef<string | null>(null);
  const speech = useMemo(() => {
    const q = new SpeechQueue((sentence) => api.speak(sentence));
    q.onSpeakingChange = setSpeaking;
    return q;
  }, [api]);

  const silence = useCallback(() => {
    speakingTurn.current = null;
    speech.stop();
  }, [speech]);

  useEffect(() => () => source.current?.close(), []);
  useEffect(() => () => speech.stop(), [speech]);
  useEffect(() => {
    if (!visible) silence();
  }, [visible, silence]);

  // "New chat": leave this conversation (it stays saved on the server) and start clean.
  const reset = () => {
    silence();
    source.current?.close();
    setConversationId(null);
    setTurns([]);
    setBusy(false);
    setText("");
    setHint(null);
  };

  const ensureConversation = useCallback(async () => {
    if (conversationId) return conversationId;
    const created = await api.createConversation();
    setConversationId(created.id);
    return created.id;
  }, [api, conversationId]);

  const patchTurn = (id: string, patch: (t: Turn) => Turn) =>
    setTurns((prev) => prev.map((t) => (t.id === id ? patch(t) : t)));

  const ask = useCallback(async (spoken?: string) => {
    const question = (spoken ?? text).trim();
    if (!question || busy) return;
    haptic.tap();
    if (spoken === undefined) setText("");
    speech.stop();
    const splitter = spoken !== undefined ? new SentenceSplitter() : null;
    setBusy(true);
    try {
      const cid = await ensureConversation();
      const accepted = await api.sendMessage(cid, question);
      if (splitter) speakingTurn.current = accepted.message_id;
      const maySpeak = () => splitter !== null && speakingTurn.current === accepted.message_id;
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
        if (maySpeak()) for (const sentence of splitter!.push(data.delta)) speech.enqueue(sentence);
      });
      es.addEventListener("done", () => {
        const rest = maySpeak() ? splitter!.flush() : null;
        if (rest) speech.enqueue(rest);
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
  }, [api, busy, ensureConversation, speech, text]);

  // Step 2: hands-free. A final utterance is asked like a typed question and its answer
  // spoken; listening resumes once that answer has finished both streaming and
  // speaking. `liveTurn` guards the gap between the utterance and the answer starting.
  const liveTurn = useRef<"idle" | "pending" | "streaming">("idle");
  const live = useLiveVoice({
    baseUrl: api.baseUrl,
    token: api.token,
    onFinal: (utterance) => {
      liveTurn.current = "pending";
      void ask(utterance);
    },
  });
  useEffect(() => {
    speech.keepMic = live.state !== "off";
  }, [speech, live.state]);
  const { state: liveState, resume: liveResume, stop: liveStop } = live;
  useEffect(() => {
    if (liveState !== "answering") return;
    if (busy) {
      liveTurn.current = "streaming";
      return;
    }
    if (liveTurn.current === "streaming" && !speaking) {
      liveTurn.current = "idle";
      liveResume();
    }
  }, [liveState, liveResume, busy, speaking]);
  useEffect(() => {
    if (!visible && liveState !== "off") liveStop();
  }, [visible, liveState, liveStop]);

  // A tap while memo is answering: stop speaking, stop generating, listen again. The
  // server keeps what was generated so far (the disconnect path, issue #45).
  const interrupt = () => {
    silence();
    source.current?.close();
    setTurns((prev) => prev.map((t) => (t.streaming ? { ...t, status: "done", streaming: false } : t)));
    setBusy(false);
    liveTurn.current = "idle";
    live.resume();
  };

  const newChat = () => {
    liveStop();
    reset();
  };

  const startListening = async () => {
    haptic.press();
    pressedAt.current = Date.now();
    released.current = false;
    setHint(null);
    silence(); // talking over the answer stops it
    try {
      const permission = await AudioModule.requestRecordingPermissionsAsync();
      if (!permission.granted) throw new Error("memo needs the microphone for this");
      await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
      await recorder.prepareToRecordAsync();
      recorder.record();
      recording.current = true;
      setVoice("listening");
      // The finger lifted while we were still setting up: a tap, not a question.
      if (released.current) await stopListening();
    } catch (e) {
      setHint(String(e));
      setVoice("idle");
    }
  };

  const stopListening = async () => {
    released.current = true;
    if (!recording.current) return; // still starting (handled there) or it failed
    recording.current = false;
    await recorder.stop();
    if (Date.now() - pressedAt.current < 400 || !recorder.uri) {
      setVoice("idle");
      setHint("Hold the mic while you talk.");
      return;
    }
    setVoice("transcribing");
    try {
      const question = await api.transcribeQuestion(recorder.uri);
      setVoice("idle");
      if (!question) {
        setHint("Didn't catch that. Try again?");
        return;
      }
      await ask(question);
    } catch (e) {
      setVoice("idle");
      setHint(`Couldn't hear that: ${String(e)}`);
    }
  };

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: keyboard }]}>
        <View style={styles.header}>
          <View style={styles.headerSide}>
            {turns.length > 0 && (
              <Pressable
                onPress={newChat}
                hitSlop={8}
                accessibilityLabel="Start a new chat"
                style={({ pressed }) => [styles.newChat, pressed && { opacity: 0.6 }]}
              >
                <Ionicons name="create-outline" size={17} color={colors.text} />
                <Text style={styles.newChatText}>New chat</Text>
              </Pressable>
            )}
          </View>
          <Text style={styles.headerTitle}>Ask</Text>
          <View style={[styles.headerSide, { alignItems: "flex-end" }]}>
            {speaking ? (
              <IconCircle name="volume-high" tone="light" onPress={silence} />
            ) : (
              <IconCircle name="close" onPress={onClose} />
            )}
          </View>
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
        {(hint || live.error) && <Text style={[ui.meta, styles.hint]}>{live.error ?? hint}</Text>}
        {live.state !== "off" ? (
          <Pressable
            onPress={live.state === "answering" ? interrupt : undefined}
            style={styles.livePanel}
            accessibilityLabel={live.state === "answering" ? "Interrupt the answer" : undefined}
          >
            <View style={styles.liveStatus}>
              {live.state === "listening" ? (
                <View style={styles.recDot} />
              ) : (
                <ActivityIndicator color={colors.muted} />
              )}
              <Text style={styles.liveText} numberOfLines={2}>
                {live.state === "connecting"
                  ? "Connecting…"
                  : live.state === "listening"
                    ? live.partial || "Listening…"
                    : speaking
                      ? "Speaking · tap to interrupt"
                      : "Thinking…"}
              </Text>
            </View>
            <IconCircle name="stop" tone="light" size={44} onPress={live.stop} />
          </Pressable>
        ) : (
        <View style={styles.composer}>
          <IconCircle
            name="headset-outline"
            size={40}
            onPress={() => {
              silence();
              void live.start();
            }}
          />
          {voice === "idle" ? (
            <TextInput
              style={styles.input}
              placeholder="Message, or hold the mic"
              placeholderTextColor={colors.muted}
              value={text}
              onChangeText={setText}
              onSubmitEditing={() => ask()}
              returnKeyType="send"
            />
          ) : (
            <View style={[styles.input, styles.listening]}>
              {voice === "listening" ? (
                <>
                  <View style={styles.recDot} />
                  <Text style={styles.listeningText}>Listening… release to ask</Text>
                </>
              ) : (
                <>
                  <ActivityIndicator color={colors.muted} />
                  <Text style={styles.listeningText}>Working out what you said…</Text>
                </>
              )}
            </View>
          )}
          {text.trim() ? (
            <IconCircle name="arrow-up" tone="light" size={40} onPress={() => ask()} />
          ) : (
            <Pressable
              onPressIn={startListening}
              onPressOut={stopListening}
              disabled={busy || voice === "transcribing"}
              accessibilityLabel="Hold to ask by voice"
              style={[styles.mic, voice === "listening" && styles.micLive]}
            >
              <Ionicons name="mic" size={20} color={voice === "listening" ? colors.text : colors.bg} />
            </Pressable>
          )}
        </View>
        )}
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
              <Pressable
                key={c.chunk_id}
                onPress={c.capture_id ? () => onOpenCapture(c.capture_id as string) : undefined}
                disabled={!c.capture_id}
                style={styles.citation}
              >
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
  headerTitle: { color: colors.text, fontSize: 17, fontWeight: "600" },
  headerSide: { width: 110, alignItems: "flex-start" },
  newChat: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    height: 34,
    borderRadius: 17,
    backgroundColor: colors.surface,
  },
  newChatText: { color: colors.text, fontSize: 14, fontWeight: "600" },
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
  userText: { color: colors.text, fontSize: 16, lineHeight: 22 },
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
  sourcesText: { color: colors.text, fontSize: 14, fontWeight: "600" },
  citation: {
    marginTop: 10,
    padding: 12,
    borderRadius: 14,
    backgroundColor: colors.surfaceAlt,
    gap: 4,
  },
  citationText: { color: colors.text, fontSize: 14, lineHeight: 20 },
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
    fontSize: 16,
    paddingHorizontal: 18,
    paddingVertical: 12,
    borderRadius: 999,
    backgroundColor: colors.surfaceAlt,
  },
  hint: { textAlign: "center", paddingHorizontal: 20, marginBottom: -4 },
  listening: { flexDirection: "row", alignItems: "center", gap: 10, minHeight: 44 },
  listeningText: { color: colors.muted, fontSize: 15 },
  recDot: { width: 10, height: 10, borderRadius: 5, backgroundColor: colors.record },
  mic: {
    width: 40,
    height: 40,
    borderRadius: 20,
    backgroundColor: colors.text,
    alignItems: "center",
    justifyContent: "center",
  },
  micLive: { backgroundColor: colors.record, transform: [{ scale: 1.15 }] },
  livePanel: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    marginHorizontal: 16,
    marginTop: 8,
    marginBottom: 28,
    padding: 14,
    borderRadius: 24,
    backgroundColor: colors.surfaceAlt,
  },
  liveStatus: { flex: 1, flexDirection: "row", alignItems: "center", gap: 10 },
  liveText: { flex: 1, color: colors.text, fontSize: 16 },
});
