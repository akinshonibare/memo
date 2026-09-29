import { useAudioStream } from "expo-audio";
import { useCallback, useEffect, useRef, useState } from "react";
import { openLiveSocket } from "./liveVoice";

// The words of a voice note as you say them (ADR 0051). Display only: the recording is
// the note, transcribed properly once it's uploaded, and these words are never saved.
// So this fails quietly - no socket, no words, and the recording carries on.
//
// It streams the mic to the same live socket Ask's voice mode uses (ADR 0025), with
// purpose "note". expo-audio is patched so a stream started during a recording shares
// the recorder's audio session instead of taking it over (patches/expo-audio+*.patch).
// Start it only once the recorder is recording, for that reason.

export type LiveWordsStatus = "off" | "connecting" | "live" | "unavailable";

export function useLiveWords({ baseUrl, token }: { baseUrl: string; token: string }) {
  const [words, setWords] = useState(""); // finished phrases
  const [partial, setPartial] = useState(""); // the phrase being said
  const [status, setStatus] = useState<LiveWordsStatus>("off");
  const socket = useRef<WebSocket | null>(null);
  const ready = useRef(false);
  const saying = useRef(""); // the partial, for keeping it when the socket closes

  const { stream } = useAudioStream({
    sampleRate: 16000,
    channels: 1,
    encoding: "int16",
    onBuffer: (buffer) => {
      const ws = socket.current;
      if (ready.current && ws && ws.readyState === WebSocket.OPEN) ws.send(buffer.data);
    },
  });

  const close = useCallback(() => {
    ready.current = false;
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
    // Words still being said when it closes stay on screen: in unbroken speech that
    // can be everything since the last pause.
    const said = saying.current;
    saying.current = "";
    if (said) setWords((w) => (w ? `${w} ${said}` : said));
    setPartial("");
  }, [stream]);

  // Paused, backgrounded or done: nothing more is heard, and what was heard stays.
  const stop = useCallback(() => {
    close();
    setStatus((s) => (s === "unavailable" ? s : "off"));
  }, [close]);

  const start = useCallback(async () => {
    if (socket.current) return;
    setStatus("connecting");
    try {
      await stream.start();
    } catch {
      setStatus("unavailable");
      return;
    }
    const ws = openLiveSocket(baseUrl, token);
    socket.current = ws;
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "start", sample_rate: stream.sampleRate || 16000, purpose: "note" }));
    };
    ws.onmessage = (event) => {
      if (typeof event.data !== "string") return;
      const message = JSON.parse(event.data) as { type: string; text?: string };
      if (message.type === "ready") {
        ready.current = true;
        setStatus("live");
      } else if (message.type === "partial") {
        saying.current = message.text ?? "";
        setPartial(saying.current);
      } else if (message.type === "final" && message.text) {
        const said = message.text;
        saying.current = "";
        setWords((w) => (w ? `${w} ${said}` : said));
        setPartial("");
      }
    };
    ws.onclose = () => {
      if (socket.current !== ws) return; // we closed it ourselves
      close();
      setStatus("unavailable");
    };
  }, [baseUrl, token, stream, close]);

  useEffect(() => close, [close]);

  return { words, partial, status, start, stop };
}
