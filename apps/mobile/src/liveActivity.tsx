import { HStack, Image, Spacer, Text, VStack } from "@expo/ui/swift-ui";
import { activityBackgroundTint, font, foregroundStyle, monospacedDigit } from "@expo/ui/swift-ui/modifiers";
import { createLiveActivity, type LiveActivity, type LiveActivityEnvironment } from "expo-widgets";
import { Platform } from "react-native";

// Recording on the Lock Screen and in the Dynamic Island (ADR 0038). While a voice note
// records, a Live Activity shows it's still going, with a clock iOS keeps ticking itself;
// tapping it comes back to memo. Like the widgets, the layout is a 'widget' function
// run in the extension's own runtime: only its props, no closures, colours written out.
//
// Props cross to native as JSON, so times are numbers and the Date is built inside.
export type RecordingProps = { startedAtMs: number; pausedAtMs: number | null };

const Recording = (p: RecordingProps, env: LiveActivityEnvironment) => {
  "widget";
  // The banner follows the Lock Screen's appearance; the Dynamic Island is always dark.
  const light = env.colorScheme === "light";
  const amber = light ? "#B06A12" : "#E2A04A";
  const cream = "#F1ECDF";
  const ink = light ? "#14261D" : cream;
  const muted = light ? "#55665C" : "#9FAEA4";
  const paused = p.pausedAtMs !== null;
  const elapsed = paused ? Math.max(0, Math.floor(((p.pausedAtMs as number) - p.startedAtMs) / 1000)) : 0;
  const still = `${Math.floor(elapsed / 60)}:${String(elapsed % 60).padStart(2, "0")}`;
  const clock = (size: number, color: string) =>
    paused ? (
      <Text modifiers={[font({ size, weight: "semibold" }), monospacedDigit(), foregroundStyle(muted)]}>{still}</Text>
    ) : (
      <Text
        date={new Date(p.startedAtMs)}
        dateStyle="timer"
        modifiers={[font({ size, weight: "semibold" }), monospacedDigit(), foregroundStyle(color)]}
      />
    );
  const mic = (size: number) => (
    <Image systemName={paused ? "pause.circle.fill" : "mic.circle.fill"} size={size} color={amber} />
  );
  const label = paused ? "Paused" : "Recording";
  return {
    banner: (
      <HStack spacing={12} modifiers={[activityBackgroundTint(light ? "#F4F0E6" : "#0B1410")]}>
        {mic(34)}
        <VStack alignment="leading" spacing={2}>
          <Text modifiers={[font({ size: 17, design: "serif" }), foregroundStyle(ink)]}>memo</Text>
          <Text modifiers={[font({ size: 13 }), foregroundStyle(muted)]}>{label}</Text>
        </VStack>
        <Spacer />
        {clock(22, ink)}
      </HStack>
    ),
    compactLeading: mic(18),
    compactTrailing: clock(14, cream),
    minimal: mic(16),
    expandedLeading: mic(30),
    expandedTrailing: clock(20, cream),
    expandedCenter: <Text modifiers={[font({ size: 15, weight: "semibold" }), foregroundStyle(cream)]}>{label}</Text>,
    expandedBottom: <Text modifiers={[font({ size: 13 }), foregroundStyle("#9FAEA4")]}>Open memo to finish the note</Text>,
  };
};

const factory = Platform.OS === "ios" ? createLiveActivity<RecordingProps>("RecordingActivity", Recording) : null;

// Starting one can fail - Live Activities off in Settings, an old iOS - and that must
// never stop the recording itself, so every call here swallows its errors.
export function startRecordingActivity(startedAtMs: number): LiveActivity<RecordingProps> | null {
  try {
    // Left over from a recording the app was killed during: gone before a new one starts.
    for (const old of factory?.getInstances() ?? []) void old.end("immediate").catch(() => {});
    return factory?.start({ startedAtMs, pausedAtMs: null }, "memo://record") ?? null;
  } catch {
    return null;
  }
}

export function updateRecordingActivity(activity: LiveActivity<RecordingProps> | null, props: RecordingProps) {
  if (!activity) return;
  void activity.update(props).catch(() => {});
}

export function endRecordingActivity(activity: LiveActivity<RecordingProps> | null) {
  if (!activity) return;
  void activity.end("immediate").catch(() => {});
}
