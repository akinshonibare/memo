import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from "expo-audio";
import { useEffect, useState } from "react";

// One player for the whole app: tapping play on a note stops whatever else was
// playing. Screens subscribe to know which note is current.
type Listener = (playingId: string | null) => void;

let player: AudioPlayer | null = null;
let currentId: string | null = null;
const listeners = new Set<Listener>();

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
  notify();
}

export async function playUrl(id: string, url: string) {
  stopPlayback();
  await setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
  const p = createAudioPlayer({ uri: url }, { updateInterval: 500 });
  p.addListener("playbackStatusUpdate", (status) => {
    if (status.didJustFinish) stopPlayback();
  });
  player = p;
  currentId = id;
  notify();
  p.play();
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
