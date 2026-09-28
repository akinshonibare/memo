import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
} from "expo-audio";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  FlatList,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Api, ApiError, type Capture } from "../api";
import { colors, ui } from "../ui";

type Pending = Capture & { optimistic?: true; localError?: string };

// One screen, one action, no categorising (issue #43). The row appears the instant
// the button is pressed; the id replaces it when the server answers.
export function CaptureScreen({ api }: { api: Api }) {
  const [text, setText] = useState("");
  const [items, setItems] = useState<Pending[]>([]);
  const [recording, setRecording] = useState(false);
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const startedAt = useRef<number>(0);

  useEffect(() => {
    api.listCaptures(20).then((page) => setItems(page.items)).catch(() => {});
  }, [api]);

  const submitText = useCallback(async () => {
    const body = text.trim();
    if (!body) return;
    setText("");
    const temp: Pending = {
      id: `local-${Date.now()}`,
      kind: "text",
      text: body,
      recording_id: null,
      status: "pending",
      failure_reason: null,
      attempt_count: 0,
      created_at: new Date().toISOString(),
      optimistic: true,
    };
    setItems((prev) => [temp, ...prev]);
    try {
      const saved = await api.createTextCapture(body);
      setItems((prev) => prev.map((c) => (c.id === temp.id ? saved : c)));
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setItems((prev) =>
        prev.map((c) => (c.id === temp.id ? { ...c, localError: message } : c)),
      );
    }
  }, [api, text]);

  const startRecording = useCallback(async () => {
    const permission = await AudioModule.requestRecordingPermissionsAsync();
    if (!permission.granted) return;
    await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
    await recorder.prepareToRecordAsync();
    recorder.record();
    startedAt.current = Date.now();
    setRecording(true);
  }, [recorder]);

  const stopRecording = useCallback(async () => {
    if (!recording) return;
    setRecording(false);
    await recorder.stop();
    const uri = recorder.uri;
    if (!uri) return;
    const durationMs = Date.now() - startedAt.current;
    const temp: Pending = {
      id: `local-${Date.now()}`,
      kind: "audio",
      text: null,
      recording_id: null,
      status: "pending",
      failure_reason: null,
      attempt_count: 0,
      created_at: new Date().toISOString(),
      optimistic: true,
    };
    setItems((prev) => [temp, ...prev]);
    try {
      const saved = await api.createAudioCapture(uri, "audio/m4a", durationMs);
      setItems((prev) => prev.map((c) => (c.id === temp.id ? saved : c)));
    } catch (error) {
      const message = error instanceof ApiError ? error.message : String(error);
      setItems((prev) =>
        prev.map((c) => (c.id === temp.id ? { ...c, localError: message } : c)),
      );
    }
  }, [api, recorder, recording]);

  return (
    <View style={ui.screen}>
      <TextInput
        style={styles.input}
        placeholder="What's on your mind?"
        placeholderTextColor={colors.muted}
        value={text}
        onChangeText={setText}
        multiline
        autoFocus
        onSubmitEditing={submitText}
        blurOnSubmit
      />
      <View style={styles.actions}>
        <Pressable
          onPress={submitText}
          style={[ui.button, !text.trim() && ui.buttonDisabled]}
          disabled={!text.trim()}
        >
          <Text style={ui.buttonText}>Save</Text>
        </Pressable>
        <Pressable
          onPressIn={startRecording}
          onPressOut={stopRecording}
          style={[ui.button, styles.record, recording && styles.recording]}
        >
          <Text style={ui.buttonText}>{recording ? "Recording… release to save" : "Hold to record"}</Text>
        </Pressable>
      </View>
      <FlatList
        data={items}
        keyExtractor={(c) => c.id}
        renderItem={({ item }) => (
          <View style={ui.card}>
            <Text style={ui.cardTitle} numberOfLines={3}>
              {item.kind === "audio" && !item.text ? "Voice note" : item.text}
            </Text>
            <Text style={ui.cardMeta}>
              {item.localError
                ? `couldn't save: ${item.localError}`
                : item.optimistic
                  ? "saving…"
                  : `${item.status}${item.failure_reason ? ` — ${item.failure_reason}` : ""}`}
            </Text>
          </View>
        )}
        ListEmptyComponent={<Text style={ui.empty}>Nothing captured yet.</Text>}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  input: {
    minHeight: 96,
    fontSize: 18,
    color: colors.text,
    padding: 12,
    borderRadius: 12,
    backgroundColor: colors.surface,
    textAlignVertical: "top",
  },
  actions: { flexDirection: "row", gap: 12, marginVertical: 12 },
  record: { flex: 1, backgroundColor: colors.accent },
  recording: { backgroundColor: colors.danger },
});
