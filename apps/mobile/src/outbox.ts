import * as FileSystem from "expo-file-system/legacy";
import { useEffect, useState } from "react";
import { type Api, ApiError, type Once, type Space, newOnce } from "./api";

// Offline capture (ADR 0028). A capture that can't reach the server is written here -
// with the time it was taken and the client token that makes a retry idempotent - and
// sent later. Audio and photos are copied out of the cache into the app's documents,
// which iOS does not purge. Nothing is dropped silently: an item the server rejects
// for good (a 4xx) stays, marked, until the user removes it or tries it again (ADR
// 0071) - and that holds for a take refused on the spot, not only one that waited.

export type OutboxItem = Once & {
  kind: "text" | "audio" | "image";
  text?: string;
  file?: string;
  mime?: string;
  durationMs?: number;
  space?: Space; // a faith note (ADR 0042) stays one when it's sent later
  attempts: number; // how many times it has been sent, by anyone
  failed?: string; // set once the server refused it for good; cleared by a retry
};

const DIR = `${FileSystem.documentDirectory}outbox/`;
const INDEX = `${DIR}index.json`;

let items: OutboxItem[] | null = null;
let flushing = false;
const listeners = new Set<(items: OutboxItem[]) => void>();
const savedListeners = new Set<() => void>();

// Told whenever a note reaches the server, however it was taken (typed, spoken, a
// photo, Siri, the share sheet) and whether now or later from the outbox.
export function onNoteSaved(listener: () => void): () => void {
  savedListeners.add(listener);
  return () => {
    savedListeners.delete(listener);
  };
}

function saved() {
  for (const l of savedListeners) l();
}

async function load(): Promise<OutboxItem[]> {
  if (items) return items;
  try {
    await FileSystem.makeDirectoryAsync(DIR, { intermediates: true });
    const raw = await FileSystem.readAsStringAsync(INDEX);
    items = JSON.parse(raw) as OutboxItem[];
  } catch {
    items = [];
  }
  return items;
}

async function save(next: OutboxItem[]) {
  items = next;
  await FileSystem.writeAsStringAsync(INDEX, JSON.stringify(next));
  for (const l of listeners) l(next);
}

// A failure worth retrying: no network, a timeout, a server error, a rate limit.
// Anything else from the server (a 4xx) will fail the same way every time.
function retryable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true; // fetch/upload threw: offline
  return error.status >= 500 || error.status === 408 || error.status === 429;
}

// The reason kept with a refused item, as a sentence: the API's message, or the
// bucket's (api.storageRefusal), without an "Error:" prefix.
function describe(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function send(api: Api, item: OutboxItem) {
  const once = { clientToken: item.clientToken, capturedAt: item.capturedAt };
  const space = item.space ?? "main";
  if (item.kind === "text") return api.createTextCapture(item.text ?? "", once, space);
  if (item.kind === "audio") return api.createAudioCapture(item.file!, item.mime, item.durationMs, once, space);
  return api.createImageCapture(item.file!, once, space, item.text?.trim() || undefined);
}

// Try to save now. If the network is the problem, keep it for later; if the server
// refuses it for good, keep it too, marked with the reason, and throw so the caller can
// say so. Either way the capture is not lost. Returns the new note's id when it was
// sent (so the app can open it), or null when it was queued.
//
// `once` is the capture's identity across attempts. A caller trying a refused take
// again passes the same one, so the retry is the very request the server already knows
// how to deduplicate, and the copy kept here by the first attempt is replaced, not
// doubled. A new identity is only right for a new capture.
export async function captureOrQueue(
  api: Api,
  capture: Pick<OutboxItem, "kind" | "text" | "file" | "mime" | "durationMs" | "space">,
  once: Once = newOnce(),
): Promise<string | null> {
  const item: OutboxItem = { ...once, ...capture, attempts: 0 };
  try {
    const made = await send(api, item);
    await drop(item.clientToken);
    saved();
    return made.id;
  } catch (error) {
    const again = retryable(error);
    await keep(item, again ? undefined : describe(error));
    if (!again) throw error;
    return null;
  }
}

// One more go at an item the server refused, from the Outbox. Same identity, same
// file, same words: only the server's answer can differ. Resolves either way; the row
// shows the new reason if it was refused again.
export async function retry(api: Api, item: OutboxItem): Promise<void> {
  const { clientToken, capturedAt, kind, text, file, mime, durationMs, space } = item;
  await captureOrQueue(api, { kind, text, file, mime, durationMs, space }, { clientToken, capturedAt }).catch(
    () => undefined,
  );
}

// Write an item to the outbox, in place of an earlier copy of the same capture. A file
// is copied out of the purgeable cache the first time; after that the kept copy is the
// one that's sent, however the retry was asked for.
async function keep(item: OutboxItem, failed: string | undefined) {
  const current = await load();
  const earlier = current.find((i) => i.clientToken === item.clientToken);
  let file = item.file;
  if (file && !file.startsWith(DIR)) {
    if (earlier?.file) {
      file = earlier.file;
    } else {
      file = `${DIR}${item.clientToken}.${item.kind === "audio" ? "m4a" : "jpg"}`;
      await FileSystem.makeDirectoryAsync(DIR, { intermediates: true });
      await FileSystem.copyAsync({ from: item.file!, to: file });
    }
  }
  const kept: OutboxItem = { ...item, file, attempts: (earlier?.attempts ?? 0) + 1, failed };
  await save(earlier ? current.map((i) => (i.clientToken === item.clientToken ? kept : i)) : [...current, kept]);
}

// The copy kept for a capture that has now reached the server, if there is one.
async function drop(clientToken: string) {
  const earlier = (await load()).find((i) => i.clientToken === clientToken);
  if (earlier) await forget(earlier);
}

// Send what's waiting, oldest first; stop at the first network failure (the rest
// would fail the same way). Safe to call often and concurrently.
export async function flushOutbox(api: Api): Promise<void> {
  if (flushing) return;
  flushing = true;
  try {
    for (const item of [...(await load())]) {
      if (item.failed) continue;
      try {
        await send(api, item);
        await forget(item);
        saved();
      } catch (error) {
        const current = (await load()).map((i) =>
          i.clientToken === item.clientToken
            ? { ...i, attempts: i.attempts + 1, failed: retryable(error) ? undefined : describe(error) }
            : i,
        );
        await save(current);
        if (retryable(error)) break;
      }
    }
  } finally {
    flushing = false;
  }
}

export async function forget(item: OutboxItem) {
  if (item.file) await FileSystem.deleteAsync(item.file, { idempotent: true }).catch(() => {});
  await save((await load()).filter((i) => i.clientToken !== item.clientToken));
}

export function useOutbox(): OutboxItem[] {
  const [state, setState] = useState<OutboxItem[]>(items ?? []);
  useEffect(() => {
    listeners.add(setState);
    load().then(setState);
    return () => {
      listeners.delete(setState);
    };
  }, []);
  return state;
}
