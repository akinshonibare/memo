import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from "expo-audio";
import { useEffect, useState } from "react";

// One player for the whole app: tapping play on a note stops whatever else was
// playing. Screens subscribe to know which note is current, and - for following a
// transcript as it plays (ADR 0039) - how far into it the player is.
type Listener = (playingId: string | null) => void;
type PositionListener = (ms: number) => void;

let player: AudioPlayer | null = null;
let currentId: string | null = null;
let positionMs = 0;
const listeners = new Set<Listener>();
const positionListeners = new Set<PositionListener>();

function notify() {
  for (const l of listeners) l(currentId);
}

export function stopPlayback() {
  if (player) {
    try {
      player.pause();
      player.remove();
    } catch {
      // already gone
    }
    player = null;
  }
  currentId = null;
  positionMs = 0;
  notify();
}

export async function playUrl(id: string, url: string) {
  stopPlayback();
  await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
  // A tenth of a second between updates: fine enough for a word to light up as it's
  // said, coarse enough to cost nothing.
  const p = createAudioPlayer({ uri: url }, { updateInterval: 100 });
  p.addListener("playbackStatusUpdate", (status) => {
    if (status.didJustFinish) {
      stopPlayback();
      return;
    }
    positionMs = Math.round((status.currentTime ?? 0) * 1000);
    for (const l of positionListeners) l(positionMs);
  });
  player = p;
  currentId = id;
  notify();
  p.play();
}

// Jump within whatever is playing (a tapped word in the transcript).
export function seekPlayback(ms: number) {
  if (!player) return;
  void player.seekTo(Math.max(0, ms) / 1000).catch(() => {});
}

export function usePlayingId(): string | null {
  const [id, setId] = useState<string | null>(currentId);
  useEffect(() => {
    listeners.add(setId);
    return () => {
      listeners.delete(setId);
    };
  }, []);
  return id;
}

// Something worked out from how far into note `id` the player is - which word is being
// said, for the transcript. The component re-renders when that changes, not on every
// tenth-of-a-second update. Mount it only while `id` is playing.
export function usePlaybackDerived<T>(id: string, derive: (ms: number) => T): T {
  const [value, setValue] = useState(() => derive(currentId === id ? positionMs : 0));
  useEffect(() => {
    const listener = (ms: number) => {
      if (currentId === id) setValue(() => derive(ms));
    };
    positionListeners.add(listener);
    return () => {
      positionListeners.delete(listener);
    };
  }, [id, derive]);
  return value;
}
