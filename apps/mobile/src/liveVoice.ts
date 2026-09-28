import { AudioModule, setAudioModeAsync, useAudioStream } from "expo-audio";
import { useCallback, useEffect, useRef, useState } from "react";

// Ask's voice mode, step 2 (ADR 0025): hands-free. The mic streams 16-bit PCM over a
// WebSocket; the server relays it to Deepgram, whose endpointing decides when a turn
// is over and sends back a `final` utterance. The caller asks it like a typed question.
//
// Half-duplex on purpose: expo-audio exposes no echo cancellation, so on the phone's
// speaker the mic would hear the answer and "interrupt" itself. While an answer plays
// the mic is muted in software (buffers are dropped, the stream and socket stay open);
// the user interrupts with a tap. Real talk-over barge-in needs iOS voice processing,
// which is a native module - the ADR's revisit trigger.

export type LiveState = "off" | "connecting" | "listening" | "answering";

const CLOSE_REASONS: Record<number, string> = {
  4401: "Your session expired. Sign in again.",
  4429: "Too many voice requests just now. Try again in a minute.",
  4503: "Voice is unavailable right now. You can still hold the mic.",
};

export function useLiveVoice({
  baseUrl,
  token,
  onFinal,
}: {
  baseUrl: string;
  token: string;
  onFinal: (text: string) => void;
}) {
  const [state, setState] = useState<LiveState>("off");
  const [partial, setPartial] = useState("");
  const [error, setError] = useState<string | null>(null);
  const socket = useRef<WebSocket | null>(null);
  const muted = useRef(true);
  const finalRef = useRef(onFinal);
  useEffect(() => {
    finalRef.current = onFinal;
  }, [onFinal]);

  const { stream } = useAudioStream({
    sampleRate: 16000,
    channels: 1,
    encoding: "int16",
    onBuffer: (buffer) => {
      const ws = socket.current;
      if (!muted.current && ws && ws.readyState === WebSocket.OPEN) ws.send(buffer.data);
    },
  });

  const stop = useCallback(() => {
    muted.current = true;
    const ws = socket.current;
    socket.current = null;
    if (ws) {
      try {
        if (ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify({ type: "stop" }));
        ws.close();
      } catch {
        // already closed
      }
    }
    try {
      stream.stop();
    } catch {
      // never started
    }
    setPartial("");
    setState("off");
    void setAudioModeAsync({ allowsRecording: false, playsInSilentMode: true });
  }, [stream]);

  const start = useCallback(async () => {
    setError(null);
    setState("connecting");
    try {
      const permission = await AudioModule.requestRecordingPermissionsAsync();
      if (!permission.granted) throw new Error("memo needs the microphone for this");
      await setAudioModeAsync({
        allowsRecording: true,
        playsInSilentMode: true,
        shouldRouteThroughEarpiece: false,
      });
      await stream.start();
    } catch (e) {
      setError(String(e));
      setState("off");
      return;
    }
    const url = `${baseUrl.replace(/^http/, "ws")}/v1/voice/live`;
    // React Native's WebSocket takes headers as a third argument; the DOM typings
    // don't know that. The token stays in a header, never in the URL.
    const Socket = WebSocket as unknown as new (
      url: string,
      protocols: string[] | undefined,
      options: { headers: Record<string, string> },
    ) => WebSocket;
    const ws = new Socket(url, undefined, { headers: { Authorization: `Bearer ${token}` } });
    ws.binaryType = "arraybuffer";
    socket.current = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "start", sample_rate: stream.sampleRate || 16000 }));
    };
    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const message = JSON.parse(event.data) as { type: string; text?: string };
      if (message.type === "ready") {
        muted.current = false;
        setState("listening");
      } else if (message.type === "partial" && !muted.current) {
        setPartial(message.text ?? "");
      } else if (message.type === "final" && !muted.current && message.text) {
        muted.current = true; // half-duplex: stop listening while the answer plays
        setPartial("");
        setState("answering");
        finalRef.current(message.text);
      }
    };
    ws.onclose = (event) => {
      if (socket.current !== ws) return; // we closed it ourselves
      setError(CLOSE_REASONS[event.code] ?? "The voice connection dropped.");
      stop();
    };
  }, [baseUrl, token, stream, stop]);

  // Called when the answer has finished (or was interrupted): listen again.
  const resume = useCallback(() => {
    if (!socket.current) return;
    setPartial("");
    muted.current = false;
    setState("listening");
  }, []);

  useEffect(() => stop, [stop]);

  return { state, partial, error, start, stop, resume };
}
