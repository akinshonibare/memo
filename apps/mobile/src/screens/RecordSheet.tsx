import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { useEffect, useRef, useState } from "react";
import { Modal, Text, View } from "react-native";
import { Api, ApiError } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { formatClock } from "../format";
import {
  endRecordingActivity,
  type RecordingProps,
  startRecordingActivity,
  updateRecordingActivity,
} from "../liveActivity";
import { levelFromDb, VoiceOrb } from "../motion";
import { useUi } from "../ui";
import { makeStyles } from "../theme";
import { haptic } from "../haptics";
import { captureOrQueue } from "../outbox";
import { LockCover } from "../lock";

// Metering on: the orb follows your voice (ADR 0032).
const OPTIONS = { ...RecordingPresets.HIGH_QUALITY, isMeteringEnabled: true };
// How long the orb takes to fall into the list before the sheet closes.
const DROP_MS = 480;

// Tap record → this sheet opens already recording. End uploads straight to the
// bucket and attaches the recording to a capture; close discards. It keeps recording
// with the phone locked or another app open (ADR 0038), shown on the Lock Screen and
// in the Dynamic Island.
export function RecordSheet({
  api,
  onClose,
  onSaved,
}: {
  api: Api;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const recorder = useAudioRecorder(OPTIONS);
  const state = useAudioRecorderState(recorder, 100);
  const [phase, setPhase] = useState<"starting" | "recording" | "paused" | "saving">("starting");
  const [error, setError] = useState<string | null>(null);
  const [drop, setDrop] = useState(false);
  const started = useRef(false);
  const activity = useRef<ReturnType<typeof startRecordingActivity>>(null);
  // What the Live Activity's clock counts from: moved on by each pause, so it shows
  // recorded time rather than time since the sheet opened.
  const clock = useRef<RecordingProps>({ startedAtMs: 0, pausedAtMs: null });

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      try {
        const permission = await AudioModule.requestRecordingPermissionsAsync();
        if (!permission.granted) throw new Error("Microphone permission was not granted.");
        await setAudioModeAsync({ allowsRecording: true, playsInSilentMode: true, allowsBackgroundRecording: true });
        await recorder.prepareToRecordAsync();
        recorder.record();
        setPhase("recording");
        clock.current = { startedAtMs: Date.now(), pausedAtMs: null };
        activity.current = startRecordingActivity(clock.current.startedAtMs);
      } catch (e) {
        setError(String(e));
      }
    })();
  }, [recorder]);

  // However the sheet goes - End, close, the app killing it - the Live Activity goes too.
  useEffect(() => () => endRecordingActivity(activity.current), []);

  // iOS can stop the recorder under us - a phone call, an alarm, Siri - and expo-audio
  // never starts it again. Show that as paused (and on the Lock Screen), with Resume,
  // rather than a clock that keeps counting while nothing is being recorded.
  const heard = useRef(false);
  const recording = state.isRecording;
  useEffect(() => {
    if (recording) {
      heard.current = true;
      return;
    }
    if (phase !== "recording" || !heard.current) return;
    heard.current = false;
    clock.current = { ...clock.current, pausedAtMs: Date.now() };
    updateRecordingActivity(activity.current, clock.current);
    setPhase("paused");
  }, [recording, phase]);

  const togglePause = () => {
    if (phase === "recording") {
      heard.current = false;
      recorder.pause();
      setPhase("paused");
      clock.current = { ...clock.current, pausedAtMs: Date.now() };
    } else if (phase === "paused") {
      heard.current = false;
      recorder.record();
      setPhase("recording");
      const { startedAtMs, pausedAtMs } = clock.current;
      clock.current = { startedAtMs: startedAtMs + (Date.now() - (pausedAtMs ?? Date.now())), pausedAtMs: null };
    }
    haptic.tap();
    updateRecordingActivity(activity.current, clock.current);
  };

  const discard = async () => {
    try {
      if (phase === "recording" || phase === "paused") await recorder.stop();
    } catch {
      // nothing to discard
    }
    endRecordingActivity(activity.current);
    activity.current = null;
    onClose();
  };

  const end = async () => {
    if (phase !== "recording" && phase !== "paused") return;
    setPhase("saving");
    try {
      const durationMs = state.durationMillis;
      await recorder.stop();
      endRecordingActivity(activity.current);
      activity.current = null;
      const uri = recorder.uri;
      if (!uri) throw new Error("No recording was produced.");
      await captureOrQueue(api, { kind: "audio", file: uri, mime: "audio/m4a", durationMs });
      haptic.done();
      // The thought becomes a dot: the orb falls toward the list, then the sheet goes.
      setDrop(true);
      setTimeout(() => {
        onSaved();
        onClose();
      }, DROP_MS);
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
          <IconCircle name="close" onPress={discard} accessibilityLabel="Discard recording" />
        </View>
        <Text style={[ui.h2, styles.title]}>New voice note</Text>
        <Text style={ui.bodyMuted}>
          {phase === "starting"
            ? "Starting the microphone…"
            : phase === "saving"
              ? drop
                ? "Saved."
                : "Uploading…"
              : live
                ? "Listening. Tap End when you're done."
                : "Paused."}
        </Text>
        {error && <Text style={[ui.danger, { marginTop: 16 }]}>{error}</Text>}
        <View style={styles.stage}>
          <VoiceOrb
            level={levelFromDb(state.metering)}
            state={phase === "saving" ? "saving" : live ? "live" : "paused"}
            drop={drop}
          />
        </View>
        <View style={styles.controls}>
          <Pill
            label={phase === "paused" ? "Resume" : "Pause"}
            onPress={togglePause}
            disabled={phase === "starting" || phase === "saving"}
          />
          <Text style={styles.clock} accessibilityLabel={`Recorded ${formatClock(state.durationMillis)}`}>
            {formatClock(state.durationMillis)}
          </Text>
          <Pill label="End" tone="light" onPress={end} busy={phase === "saving" && !drop} disabled={phase === "starting"} />
        </View>
        <LockCover />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((colors) => ({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24, paddingBottom: 40 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  title: { marginTop: 12, marginBottom: 6 },
  stage: { flex: 1, alignItems: "center", justifyContent: "center" },
  controls: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  clock: { color: colors.text, fontSize: 17, fontWeight: "600", fontVariant: ["tabular-nums"] },
}));
