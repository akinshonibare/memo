import * as Crypto from "expo-crypto";
import * as FileSystem from "expo-file-system/legacy";
import type { Config } from "./config";
import { toBase64 } from "./voice";

export type Capture = {
  id: string;
  kind: "text" | "audio" | "image";
  text: string | null;
  recording_id: string | null;
  status: "pending" | "transcribing" | "describing" | "extracting" | "embedding" | "ready" | "failed";
  failure_reason: string | null;
  attempt_count: number;
  created_at: string;
  duration_ms: number | null;
};

export type Derivation = {
  id: string;
  // null once the note it came from is deleted: the item is kept (ADR 0022).
  capture_id: string | null;
  kind: "todo" | "event" | "idea";
  payload: Record<string, unknown> & { title?: string };
  status: "pending_review" | "confirmed" | "rejected" | "user_created";
  extractor_version: string;
  confidence: number | null;
  completed_at: string | null; // todos only: when it was ticked off
  created_at: string;
};

export type CaptureDetail = Capture & { derivations: Derivation[] };

export type Citation = {
  chunk_id: string;
  capture_id: string | null;
  derivation_id: string | null;
  rank: number;
  snippet: string;
  created_at: string;
};

export type Message = {
  id: string;
  role: "user" | "assistant";
  text: string | null;
  status: "pending" | "streaming" | "done" | "failed";
  error: string | null;
  citations: Citation[];
};

// GET /v1/morning (issue #38): built overnight at 04:00 local, on demand the first time.
export type MorningView = {
  local_date: string;
  timezone: string;
  todos: { id: string; capture_id: string | null; title: string; due: string | null; notes: string | null }[];
  events: {
    id: string;
    capture_id: string | null;
    title: string;
    starts_at: string;
    ends_at: string | null;
    location: string | null;
  }[];
  resurfaced_idea: {
    id: string;
    capture_id: string | null;
    title: string;
    summary: string;
    captured_on: string;
  } | null;
  built_at: string;
};

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter?: number,
  ) {
    super(message);
  }
}

export type Me = {
  id: string;
  timezone: string;
  name: string | null; // what the assistant calls you; null means "you"
  email: string | null; // from Apple, possibly a private relay address
  voice: string | null; // Aura-2 voice for spoken answers; null = default
  created_at: string;
};

export type Voice = { id: string; label: string };

export type Usage = { window_days: number; total_cost_usd: string };

export class Api {
  // Called once when the server says the session is gone (401): the app signs out.
  onUnauthorized: (() => void) | null = null;

  constructor(private config: Config) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.config.baseUrl}${path}`, {
      ...init,
      headers: {
        ...(this.config.token ? { Authorization: `Bearer ${this.config.token}` } : {}),
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
    if (response.status === 204) return undefined as T;
    if (!response.ok) {
      if (response.status === 401 && this.config.token) this.onUnauthorized?.();
      const body = await response.text();
      const retryAfter = Number(response.headers.get("Retry-After") ?? "");
      throw new ApiError(
        response.status,
        body.slice(0, 300) || response.statusText,
        Number.isFinite(retryAfter) ? retryAfter : undefined,
      );
    }
    return (await response.json()) as T;
  }

  health() {
    return fetch(`${this.config.baseUrl}/health`).then((r) => r.ok);
  }

  // --- auth (ADR 0019) ------------------------------------------------------------

  loginWithApple(body: { identity_token: string; full_name?: string; device_name?: string }) {
    return this.request<{ token: string; user: Me }>("/v1/auth/apple", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  logout() {
    return this.request<void>("/v1/auth/logout", { method: "POST" });
  }

  // --- me -----------------------------------------------------------------------

  getMe() {
    return this.request<Me>("/v1/me");
  }

  // The morning view and date extraction run in this zone, so it comes from the
  // device rather than a server-side config value.
  setTimezone(timezone: string) {
    return this.updateProfile({ timezone });
  }

  updateProfile(patch: { name?: string; voice?: string | null; timezone?: string }) {
    return this.request<Me>("/v1/me", { method: "PATCH", body: JSON.stringify(patch) });
  }

  listVoices() {
    return this.request<Voice[]>("/v1/me/voices");
  }

  getUsage(days: number) {
    return this.request<Usage>(`/v1/usage?days=${days}`);
  }

  logoutEverywhere() {
    return this.request<void>("/v1/auth/logout-all", { method: "POST" });
  }

  // App Store guideline 5.1.1(v): an account made in the app can be deleted in it.
  deleteAccount(userId: string) {
    return this.request<void>(`/v1/me?confirm=${encodeURIComponent(userId)}`, { method: "DELETE" });
  }

  // --- morning ------------------------------------------------------------------

  getMorning() {
    return this.request<MorningView>("/v1/morning");
  }

  // --- your data (issue #49) --------------------------------------------------------

  exportEverything() {
    return this.request<unknown>("/v1/me/export");
  }

  // The server makes you name your own user id, so a stray call can't do this.
  deleteEverything(userId: string) {
    return this.request<{ deleted: Record<string, number> }>(
      `/v1/me/data?confirm=${encodeURIComponent(userId)}`,
      { method: "DELETE" },
    );
  }

  // --- captures -----------------------------------------------------------------

  createTextCapture(text: string) {
    return this.request<Capture>("/v1/captures", {
      method: "POST",
      body: JSON.stringify({ text, client_token: Crypto.randomUUID() }),
    });
  }

  async createAudioCapture(fileUri: string, mime = "audio/m4a", durationMs?: number) {
    const presigned = await this.request<{
      recording_id: string;
      upload_url: string;
    }>("/v1/recordings", {
      method: "POST",
      body: JSON.stringify({ mime, duration_ms: durationMs }),
    });
    // Bytes go straight to the bucket; the API never touches them (design doc, Step 3).
    const upload = await FileSystem.uploadAsync(presigned.upload_url, fileUri, {
      httpMethod: "PUT",
      headers: { "Content-Type": mime },
    });
    if (upload.status < 200 || upload.status >= 300) {
      throw new ApiError(upload.status, "upload to storage failed");
    }
    return this.request<Capture>("/v1/captures", {
      method: "POST",
      body: JSON.stringify({
        recording_id: presigned.recording_id,
        client_token: Crypto.randomUUID(),
      }),
    });
  }

  // A photo (ADR 0024): resized JPEG, uploaded like a recording, described on the server.
  async createImageCapture(fileUri: string) {
    const presigned = await this.request<{ recording_id: string; upload_url: string }>(
      "/v1/recordings",
      { method: "POST", body: JSON.stringify({ mime: "image/jpeg" }) },
    );
    const upload = await FileSystem.uploadAsync(presigned.upload_url, fileUri, {
      httpMethod: "PUT",
      headers: { "Content-Type": "image/jpeg" },
    });
    if (upload.status < 200 || upload.status >= 300) {
      throw new ApiError(upload.status, "upload to storage failed");
    }
    return this.request<Capture>("/v1/captures", {
      method: "POST",
      body: JSON.stringify({
        recording_id: presigned.recording_id,
        client_token: Crypto.randomUUID(),
      }),
    });
  }

  listCaptures(limit = 50) {
    return this.request<{ items: Capture[]; next_cursor: string | null }>(
      `/v1/captures?limit=${limit}`,
    );
  }

  getCapture(id: string) {
    return this.request<CaptureDetail>(`/v1/captures/${id}`);
  }

  retryCapture(id: string) {
    return this.request<Capture>(`/v1/captures/${id}/retry`, { method: "POST" });
  }

  deleteCapture(id: string) {
    return this.request<void>(`/v1/captures/${id}`, { method: "DELETE" });
  }

  // The recording or photo behind a capture: a short-lived URL straight to the bucket.
  getMedia(id: string) {
    return this.request<{ url: string; expires_at: string; mime: string; duration_ms: number | null }>(
      `/v1/captures/${id}/media`,
    );
  }

  getAudio(id: string) {
    return this.getMedia(id);
  }

  // --- derivations --------------------------------------------------------------

  // Newest first; kept derivations of deleted notes included (capture_id null).
  async listDerivations(max = 400): Promise<Derivation[]> {
    const items: Derivation[] = [];
    let cursor: string | null = null;
    do {
      const query: string = cursor ? `&cursor=${encodeURIComponent(cursor)}` : "";
      const page: { items: Derivation[]; next_cursor: string | null } = await this.request(
        `/v1/derivations?limit=200${query}`,
      );
      items.push(...page.items);
      cursor = page.next_cursor;
    } while (cursor && items.length < max);
    return items;
  }

  // Added straight into the Planner, with no note behind it.
  createDerivation(kind: Derivation["kind"], payload: object) {
    return this.request<Derivation>("/v1/derivations", {
      method: "POST",
      body: JSON.stringify({ kind, payload }),
    });
  }

  reviewDerivation(
    id: string,
    body: { status?: "confirmed" | "rejected"; payload?: object; completed?: boolean },
  ) {
    return this.request<Derivation>(`/v1/derivations/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    });
  }

  addDerivation(captureId: string, kind: Derivation["kind"], payload: object) {
    return this.request<Derivation>(`/v1/captures/${captureId}/derivations`, {
      method: "POST",
      body: JSON.stringify({ kind, payload }),
    });
  }

  // --- voice (ADR 0025) ---------------------------------------------------------

  // A spoken question, as text. The clip goes to the API and is never stored.
  async transcribeQuestion(fileUri: string, mime = "audio/m4a"): Promise<string> {
    const response = await FileSystem.uploadAsync(`${this.config.baseUrl}/v1/voice/transcribe`, fileUri, {
      httpMethod: "POST",
      uploadType: FileSystem.FileSystemUploadType.BINARY_CONTENT,
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": mime },
    });
    if (response.status === 401) this.onUnauthorized?.();
    if (response.status < 200 || response.status >= 300) {
      throw new ApiError(response.status, response.body.slice(0, 300));
    }
    return (JSON.parse(response.body) as { text: string }).text;
  }

  // One sentence of an answer as speech, saved to a cache file the player can open.
  // `voice` only for previewing one in Profile; answers use the saved choice.
  async speak(text: string, voice?: string): Promise<string> {
    const response = await fetch(`${this.config.baseUrl}/v1/voice/speak`, {
      method: "POST",
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(voice ? { text, voice } : { text }),
    });
    if (!response.ok) throw new ApiError(response.status, (await response.text()).slice(0, 300));
    const uri = `${FileSystem.cacheDirectory}speech-${Date.now()}-${Math.random().toString(36).slice(2)}.mp3`;
    await FileSystem.writeAsStringAsync(uri, toBase64(await response.arrayBuffer()), {
      encoding: FileSystem.EncodingType.Base64,
    });
    return uri;
  }

  // --- ask ----------------------------------------------------------------------

  createConversation() {
    return this.request<{ id: string }>("/v1/conversations", { method: "POST" });
  }

  listMessages(conversationId: string) {
    return this.request<Message[]>(`/v1/conversations/${conversationId}/messages`);
  }

  sendMessage(conversationId: string, text: string) {
    return this.request<{ message_id: string; stream_url: string }>(
      `/v1/conversations/${conversationId}/messages`,
      { method: "POST", body: JSON.stringify({ text }) },
    );
  }

  streamUrl(streamPath: string) {
    return `${this.config.baseUrl}${streamPath}`;
  }

  get token() {
    return this.config.token ?? "";
  }

  get baseUrl() {
    return this.config.baseUrl;
  }
}
