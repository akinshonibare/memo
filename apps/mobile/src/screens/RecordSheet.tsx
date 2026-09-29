import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Modal, Text, View } from "react-native";
import { Api, ApiError, type Space } from "../api";
import { confirmDiscard, IconCircle, Pill, SheetHandle } from "../components";
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
import { Dialogs } from "../dialogs";
import { LockCover } from "../lock";

// Metering on: the orb follows your voice (ADR 0032).
const OPTIONS = { ...RecordingPresets.HIGH_QUALITY, isMeteringEnabled: true };
const MODE = { allowsRecording: true, playsInSilentMode: true, allowsBackgroundRecording: true };
// iOS keeps the microphone for a phone call. An app asking for it then gets one of
// these audio-session errors (Apple's AudioSessionTypes.h): '!pri' insufficient
// priority, "another app (Phone, etc.) is controlling it"; '!int' can't interrupt
// others; '!act' busy. No app can record during a call, so the sheet waits instead.
const MIC_BUSY = /561017449|560557684|560030580|insufficient ?priority|cannot ?interrupt/i;
const RETRY_MS = 2000;

export function micBusy(error: unknown): boolean {
  return MIC_BUSY.test(String(error));
}
// How long the orb takes to fall into the list before the sheet closes.
const DROP_MS = 480;

// Tap record → this sheet opens already recording. End uploads straight to the
// bucket and attaches the recording to a capture; close discards. It keeps recording
// with the phone locked or another app open (ADR 0038), shown on the Lock Screen and
// in the Dynamic Island. While it's open, your words appear as you say them (ADR 0051).
export function RecordSheet({
  api,
  space = "main",
  onClose,
  onSaved,
  onType,
}: {
  api: Api;
  space?: Space; // recorded from the Faith screen: a faith note (ADR 0042)
  onClose: () => void;
  onSaved: (captureId: string | null) => void; // null: kept offline, sent later
  onType?: () => void; // the microphone is busy: write it down instead
}) {
  const ui = useUi();
  const styles = useStyles();
  const recorder = useAudioRecorder(OPTIONS);
  const state = useAudioRecorderState(recorder, 100);
  // "waiting": the microphone is busy (a call), and the sheet starts as soon as it's free.
  const [phase, setPhase] = useState<"starting" | "waiting" | "recording" | "paused" | "saving">("starting");
  const [error, setError] = useState<string | null>(null);
  const [onCall, setOnCall] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [drop, setDrop] = useState(false);
  const started = useRef(false);
  const activity = useRef<ReturnType<typeof startRecordingActivity>>(null);
  // What the Live Activity's clock counts from: moved on by each pause, so it shows
  // recorded time rather than time since the sheet opened.
  const clock = useRef<RecordingProps>({ startedAtMs: 0, pausedAtMs: null });
  const [foreground, setForeground] = useState(AppState.currentState === "active");
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => setForeground(s === "active"));
    return () => sub.remove();
  }, []);

  // Take the microphone and start. When it's busy - a call - wait rather than fail:
  // no app can record during a call, and the thought is still worth keeping.
  const trying = useRef(false);
  const begin = useCallback(async () => {
    if (trying.current) return false;
    trying.current = true;
    try {
      await setAudioModeAsync(MODE);
      await recorder.prepareToRecordAsync();
      recorder.record();
    } catch (e) {
      if (!micBusy(e)) console.warn("record: microphone unavailable", e);
      setOnCall(micBusy(e));
      setPhase("waiting");
      return false;
    } finally {
      trying.current = false;
    }
    setPhase("recording");
    clock.current = { startedAtMs: Date.now(), pausedAtMs: null };
    activity.current = startRecordingActivity(clock.current.startedAtMs);
    return true;
  }, [recorder]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    (async () => {
      const permission = await AudioModule.requestRecordingPermissionsAsync().catch(() => null);
      if (!permission?.granted) {
        setError("memo needs the microphone. You can allow it in the iPhone's Settings → memo.");
        return;
      }
      await begin();
    })();
  }, [begin]);

  // Waiting for the call to end: try again every couple of seconds while memo is open
  // (iOS won't let a recording start from the background), and start the moment the
  // microphone is free.
  useEffect(() => {
    if (phase !== "waiting" || !foreground) return;
    const timer = setInterval(() => {
      void begin().then((ok) => ok && haptic.press());
    }, RETRY_MS);
    return () => clearInterval(timer);
  }, [phase, foreground, begin]);

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

  const togglePause = async () => {
    if (phase === "recording") {
      heard.current = false;
      recorder.pause();
      setPhase("paused");
      clock.current = { ...clock.current, pausedAtMs: Date.now() };
    } else if (phase === "paused") {
      // A call pauses a take (iOS takes the microphone back). Get it back before
      // resuming: without it the recorder records nothing while the clock runs.
      try {
        await setAudioModeAsync(MODE);
      } catch (e) {
        haptic.warn();
        setNotice(
          micBusy(e)
            ? "Still on a call: the iPhone keeps the microphone until it ends. Resume after."
            : "memo can't get the microphone back yet. Try Resume again in a moment.",
        );
        return;
      }
      setNotice(null);
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
      const id = await captureOrQueue(api, { kind: "audio", file: uri, mime: "audio/m4a", durationMs, space });
      haptic.done();
      // The thought becomes a dot: the orb falls toward the list, then the sheet goes.
      setDrop(true);
      setTimeout(() => {
        onSaved(id);
        onClose();
      }, DROP_MS);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setPhase("paused");
    }
  };

  // A swipe down closes the sheet like the close button, except that a recording in
  // progress is asked about first: a stray swipe shouldn't cost a take.
  const recordingNow = phase === "recording" || phase === "paused";
  const requestClose = () => {
    if (phase === "saving") return;
    if (recordingNow) confirmDiscard("Discard this recording?", "Discard", () => void discard(), "Keep recording");
    else void discard();
  };

  const live = phase === "recording";
  return (
    <Modal
      visible
      animationType="slide"
      presentationStyle="pageSheet"
      allowSwipeDismissal={!recordingNow && phase !== "saving"}
      onRequestClose={requestClose}
    >
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.top}>
          <View style={{ flex: 1 }} />
          <IconCircle name="close" onPress={discard} accessibilityLabel="Discard recording" />
        </View>
        <Text style={[ui.h2, styles.title]}>{space === "faith" ? "New reflection" : "New voice note"}</Text>
        <Text style={ui.bodyMuted}>
          {phase === "starting"
            ? error
              ? "The microphone is off."
              : "Starting the microphone…"
            : phase === "waiting"
              ? onCall
                ? "You're on a call, and the iPhone keeps the microphone for it. memo will start recording as soon as the call ends."
                : "memo can't use the microphone right now. It will start as soon as it can."
              : phase === "saving"
                ? drop
                  ? "Saved."
                  : "Uploading…"
                : live
                  ? "Listening. Tap End when you're done."
                  : "Paused."}
        </Text>
        {notice && phase === "paused" && <Text style={[ui.meta, { marginTop: 12 }]}>{notice}</Text>}
        {error && <Text style={[ui.danger, { marginTop: 16 }]}>{error}</Text>}
        <View style={styles.stage}>
          <VoiceOrb
            level={levelFromDb(state.metering)}
            state={phase === "saving" ? "saving" : live ? "live" : "paused"}
            drop={drop}
          />
        </View>
        {phase === "waiting" ? (
          // Nothing to pause or end yet: the one useful thing is to write it down now.
          onType ? (
            <View style={styles.waiting}>
              <Pill label="Type it instead" icon="create-outline" tone="light" onPress={onType} />
            </View>
          ) : null
        ) : (
          <View style={styles.controls}>
            <Pill
              label={phase === "paused" ? "Resume" : "Pause"}
              onPress={() => void togglePause()}
              disabled={phase === "starting" || phase === "saving"}
            />
            <Text style={styles.clock} accessibilityLabel={`Recorded ${formatClock(state.durationMillis)}`}>
              {formatClock(state.durationMillis)}
            </Text>
            <Pill label="End" tone="light" onPress={end} busy={phase === "saving" && !drop} disabled={phase === "starting"} />
          </View>
        )}
        <Dialogs />
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
  waiting: { alignItems: "center" },
  clock: { color: colors.text, fontSize: 17, fontWeight: "600", fontVariant: ["tabular-nums"] },
}));
