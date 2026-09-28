import { createAudioPlayer, setAudioModeAsync, type AudioPlayer } from "expo-audio";
import { stopPlayback } from "./playback";

// Ask's voice mode, step 1 (ADR 0025). Answers stream in as text tokens; this turns
// them into sentences and speaks each one as soon as it is complete, so the first
// sentence is heard while the rest is still being written.

const MIN_SENTENCE = 24; // shorter fragments ("Yes.") are held and joined to the next

// Splits a token stream into speakable sentences. Deliberately simple: a sentence
// ends at . ! ? or a newline followed by whitespace, once it is long enough to be
// worth a request of its own.
export class SentenceSplitter {
  private buffer = "";

  push(delta: string): string[] {
    this.buffer += delta;
    const out: string[] = [];
    const boundary = /[.!?](?:["')\]]*)\s+|\n+/g;
    let start = 0;
    let match: RegExpExecArray | null;
    while ((match = boundary.exec(this.buffer)) !== null) {
      const end = match.index + match[0].length;
      const candidate = this.buffer.slice(start, end).trim();
      if (candidate.length >= MIN_SENTENCE) {
        out.push(candidate);
        start = end;
      }
    }
    this.buffer = this.buffer.slice(start);
    return out;
  }

  flush(): string | null {
    const rest = this.buffer.trim();
    this.buffer = "";
    return rest || null;
  }
}

type Fetcher = (text: string) => Promise<string>; // text -> local mp3 file uri

// Plays sentences strictly in order. Audio for the next sentence is fetched while the
// current one plays, so the gap between sentences is playback, not network.
export class SpeechQueue {
  private queue: Promise<string | null>[] = [];
  private player: AudioPlayer | null = null;
  private finishCurrent: (() => void) | null = null;
  private running = false;
  private generation = 0;
  onSpeakingChange: ((speaking: boolean) => void) | null = null;
  // Live mode keeps the mic stream open while speaking (it is muted in software, not
  // released), so the session must stay in record mode but route to the loudspeaker.
  keepMic = false;

  constructor(private fetchAudio: Fetcher) {}

  enqueue(text: string) {
    const gen = this.generation;
    // Start the request now; failures become a skipped sentence, not a stuck queue.
    this.queue.push(this.fetchAudio(text).catch(() => null));
    if (!this.running) void this.drain(gen);
  }

  // Everything stops: what is playing, and everything fetched or queued after it.
  stop() {
    this.generation += 1;
    this.queue = [];
    this.release();
    this.setRunning(false);
  }

  private async drain(gen: number) {
    this.setRunning(true);
    stopPlayback(); // a note's recording and an answer never talk over each other
    // Recording mode routes output to the earpiece on iOS; speaking wants the speaker.
    await setAudioModeAsync(
      this.keepMic
        ? { allowsRecording: true, playsInSilentMode: true, shouldRouteThroughEarpiece: false }
        : { allowsRecording: false, playsInSilentMode: true },
    );
    while (this.queue.length > 0 && gen === this.generation) {
      const uri = await this.queue.shift()!;
      if (!uri || gen !== this.generation) continue;
      await this.play(uri, gen);
    }
    if (gen === this.generation) this.setRunning(false);
  }

  private play(uri: string, gen: number): Promise<void> {
    return new Promise((resolve) => {
      const p = createAudioPlayer({ uri });
      this.player = p;
      // Settled by finishing or by stop(), whichever comes first.
      this.finishCurrent = resolve;
      p.addListener("playbackStatusUpdate", (status) => {
        if (status.didJustFinish || gen !== this.generation) this.release();
      });
      p.play();
    });
  }

  private release() {
    const p = this.player;
    const finish = this.finishCurrent;
    this.player = null;
    this.finishCurrent = null;
    finish?.();
    if (!p) return;
    try {
      p.pause();
      p.remove();
    } catch {
      // already gone
    }
  }

  private setRunning(running: boolean) {
    if (this.running === running) return;
    this.running = running;
    this.onSpeakingChange?.(running);
  }
}

// ArrayBuffer -> base64, in chunks so a long clip doesn't blow the argument limit.
export function toBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}
