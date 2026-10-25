import {
  AudioModule,
  RecordingPresets,
  setAudioModeAsync,
  useAudioRecorder,
  useAudioRecorderState,
} from "expo-audio";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Modal, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Api, ApiError, newOnce, type Once, type Space } from "../api";
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
import { type LiveWordsStatus, useLiveWords } from "../liveWords";

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
// bucket and attaches the recording to a capture; close discards - except a take the
// server refused, which is kept on the phone (ADR 0071). It keeps recording
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
  // "kept": End was refused for good; the take is safe on the phone and can be tried again.
  const [phase, setPhase] = useState<"starting" | "waiting" | "recording" | "paused" | "saving" | "kept">(
    "starting",
  );
  const [error, setError] = useState<string | null>(null);
  const [onCall, setOnCall] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [drop, setDrop] = useState(false);
  const started = useRef(false);
  const activity = useRef<ReturnType<typeof startRecordingActivity>>(null);
  // The take, fixed the moment End is first tapped: this file, this length, this
  // identity. Trying again sends exactly that, so the server sees one capture however
  // many times it's asked (its client_token is the same), and nothing is read back from
  // a recorder that has already stopped - stopped, expo-audio reports a length of 0.
  // State rather than a ref: the clock shows its length once the recorder has stopped.
  const [take, setTake] = useState<{ uri: string; durationMs: number | undefined; once: Once } | null>(null);
  // What the Live Activity's clock counts from: moved on by each pause, so it shows
  // recorded time rather than time since the sheet opened.
  const clock = useRef<RecordingProps>({ startedAtMs: 0, pausedAtMs: null });
  // What you're saying, as you say it (ADR 0051): only while recording with the app
  // open. Paused, in the background or saving, nothing is streamed.
  const transcript = useLiveWords({ baseUrl: api.baseUrl, token: api.token });
  const [foreground, setForeground] = useState(AppState.currentState === "active");
  useEffect(() => {
    const sub = AppState.addEventListener("change", (s) => setForeground(s === "active"));
    return () => sub.remove();
  }, []);
  const { start: listen, stop: stopListening } = transcript;
  useEffect(() => {
    if (phase === "recording" && foreground) void listen();
    else stopListening();
  }, [phase, foreground, listen, stopListening]);

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
  // microphone is free. (`foreground` is the same one the live words follow.)
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
      stopListening();
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
    stopListening();
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
    if (phase !== "recording" && phase !== "paused" && phase !== "kept") return;
    setPhase("saving");
    stopListening();
    let fixed = take;
    try {
      if (!fixed) {
        const durationMs = state.durationMillis;
        await recorder.stop();
        endRecordingActivity(activity.current);
        activity.current = null;
        const uri = recorder.uri;
        if (!uri) throw new Error("No recording was produced.");
        // 0 is "unknown" (ended before the first status tick, or iOS stopped the
        // recorder under us); the server fills the length in from the transcript.
        fixed = { uri, durationMs: durationMs > 0 ? durationMs : undefined, once: newOnce() };
        setTake(fixed);
      }
      const { uri, durationMs, once } = fixed;
      const id = await captureOrQueue(api, { kind: "audio", file: uri, mime: "audio/m4a", durationMs, space }, once);
      setError(null);
      haptic.done();
      // The thought becomes a dot: the orb falls toward the list, then the sheet goes.
      setDrop(true);
      setTimeout(() => {
        onSaved(id);
        onClose();
      }, DROP_MS);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      // Refused for good: the outbox has a copy by now, so the take is safe whatever
      // happens to this sheet. Failed before there was a take: back to the recorder.
      setPhase(fixed ? "kept" : "paused");
    }
  };

  // A swipe down closes the sheet like the close button, except that a recording in
  // progress is asked about first: a stray swipe shouldn't cost a take. A kept take
  // costs nothing to close: it's in the outbox either way.
  const recordingNow = phase === "recording" || phase === "paused";
  const kept = phase === "kept";
  const clockMs = take?.durationMs ?? state.durationMillis;
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
          <IconCircle name="close" onPress={discard} accessibilityLabel={kept ? "Close" : "Discard recording"} />
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
                : kept
                  ? "Not saved yet. The take is kept on this phone: try again, or close and find it under On this phone."
                  : live
                    ? "Listening. Tap the orb or End when you're done."
                    : "Paused."}
        </Text>
        {notice && phase === "paused" && <Text style={[ui.meta, { marginTop: 12 }]}>{notice}</Text>}
        {error && <Text style={[ui.danger, { marginTop: 16 }]}>{error}</Text>}
        <LiveTranscript words={transcript.words} partial={transcript.partial} status={transcript.status} />
        <View style={styles.stage}>
          <VoiceOrb
            level={levelFromDb(state.metering)}
            state={phase === "saving" ? "saving" : live ? "live" : "paused"}
            drop={drop}
          />
          {/* The orb is the one big thing on the screen, so a tap on it ends the take,
              like End. The target is the whole middle of the sheet, on a layer of its
              own above the orb: the orb's halos and body move with your voice, and a
              tap must never depend on where they happen to be. While starting, waiting
              or saving it does nothing; on a kept take it tries again. */}
          <Pressable
            style={StyleSheet.absoluteFill}
            onPress={() => {
              haptic.tap();
              void end();
            }}
            disabled={!recordingNow && !kept}
            accessibilityRole="button"
            accessibilityLabel={kept ? "Try again" : "End recording"}
            accessibilityHint="Saves the voice note"
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
              disabled={phase === "starting" || phase === "saving" || kept}
            />
            <Text style={styles.clock} accessibilityLabel={`Recorded ${formatClock(clockMs)}`}>
              {formatClock(clockMs)}
            </Text>
            <Pill
              label={kept ? "Try again" : "End"}
              tone="light"
              onPress={end}
              busy={phase === "saving" && !drop}
              disabled={phase === "starting"}
            />
          </View>
        )}
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

// The words so far, newest at the bottom. One colour: in unbroken speech the server
// holds everything as "being said" until a pause, so a lighter partial greys it all.
function LiveTranscript({ words, partial, status }: { words: string; partial: string; status: LiveWordsStatus }) {
  const ui = useUi();
  const styles = useStyles();
  const scroll = useRef<ScrollView>(null);
  if (!words && !partial) {
    return status === "unavailable" ? (
      <Text style={[ui.meta, styles.transcriptNote]}>{"Live words aren't available right now. The recording is fine."}</Text>
    ) : null;
  }
  return (
    <ScrollView
      ref={scroll}
      style={styles.transcript}
      onContentSizeChange={() => scroll.current?.scrollToEnd({ animated: true })}
      accessibilityLabel="What you've said so far"
    >
      <Text style={styles.words}>{[words, partial].filter(Boolean).join(" ")}</Text>
    </ScrollView>
  );
}

const useStyles = makeStyles((colors) => ({
  transcript: { maxHeight: 190, marginTop: 18, flexGrow: 0 },
  transcriptNote: { marginTop: 18 },
  words: { color: colors.text, fontSize: 18, lineHeight: 26 },
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24, paddingBottom: 40 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  title: { marginTop: 12, marginBottom: 6 },
  stage: { flex: 1, alignItems: "center", justifyContent: "center" },
  controls: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  waiting: { alignItems: "center" },
  clock: { color: colors.text, fontSize: 17, fontWeight: "600", fontVariant: ["tabular-nums"] },
}));
