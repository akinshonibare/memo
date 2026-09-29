import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { useEffect, useRef, useState } from "react";
import { Modal, StyleSheet, Text, View } from "react-native";
import { Api, ApiError } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { formatClock } from "../format";
import { colors, ui } from "../ui";
import { haptic } from "../haptics";
import { captureOrQueue } from "../outbox";

// Tap record → this sheet opens already recording. End uploads straight to the
// bucket and attaches the recording to a capture; close discards.
export function RecordSheet({
  api,
  onClose,
  onSaved,
}: {
  api: Api;
  onClose: () => void;
  onSaved: () => void;
}) {
  const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
  const state = useAudioRecorderState(recorder, 250);
  const [phase, setPhase] = useState<"starting" | "recording" | "paused" | "saving">("starting");
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const permission = await AudioModule.requestRecordingPermissionsAsync();
        if (!permission.granted) throw new Error("Microphone permission was not granted.");
        await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true });
        await recorder.prepareToRecordAsync();
        recorder.record();
        setPhase("recording");
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [recorder]);

  const togglePause = () => {
    if (phase === "recording") {
      recorder.pause();
      setPhase("paused");
    } else if (phase === "paused") {
      recorder.record();
      setPhase("recording");
    }
  };

  const discard = async () => {
    try {
      if (phase === "recording" || phase === "paused") await recorder.stop();
    } catch {
      // nothing to discard
    }
    onClose();
  };

  const end = async () => {
    if (phase !== "recording" && phase !== "paused") return;
    setPhase("saving");
    try {
      const durationMs = state.durationMillis;
      await recorder.stop();
      const uri = recorder.uri;
      if (!uri) throw new Error("No recording was produced.");
      await captureOrQueue(api, { kind: "audio", file: uri, mime: "audio/m4a", durationMs });
      haptic.done();
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setPhase("paused");
    }
  };

  const live = phase === "recording";
  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={discard}>
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.top}>
          <View style={{ flex: 1 }} />
          <IconCircle name="close" onPress={discard} />
        </View>
        <Text style={[ui.h2, styles.title]}>New voice note</Text>
        <Text style={ui.bodyMuted}>
          {phase === "starting"
            ? "Starting the microphone…"
            : phase === "saving"
              ? "Uploading…"
              : live
                ? "Listening. Tap End when you're done."
                : "Paused."}
        </Text>
        {error && <Text style={[ui.danger, { marginTop: 16 }]}>{error}</Text>}
        <View style={{ flex: 1 }} />
        <View style={styles.controls}>
          <Pill
            label={phase === "paused" ? "Resume" : "Pause"}
            onPress={togglePause}
            disabled={phase === "starting" || phase === "saving"}
          />
          <View style={styles.meter}>
            <View style={[styles.dot, live && styles.dotLive]} />
            <View style={[styles.dot, live && styles.dotLive]} />
            <View style={[styles.dot, live && styles.dotLive]} />
            <Text style={styles.clock}>{formatClock(state.durationMillis)}</Text>
          </View>
          <Pill label="End" tone="light" onPress={end} busy={phase === "saving"} disabled={phase === "starting"} />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24, paddingBottom: 40 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  title: { marginTop: 12, marginBottom: 6 },
  controls: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  meter: { flexDirection: "row", alignItems: "center", gap: 5 },
  dot: { width: 14, height: 14, borderRadius: 7, backgroundColor: colors.faint },
  dotLive: { backgroundColor: colors.green },
  clock: { color: colors.text, fontSize: 15, fontWeight: "600", marginLeft: 10, fontVariant: ["tabular-nums"] },
});
