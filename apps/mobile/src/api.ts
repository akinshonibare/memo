import * as Crypto from "expo-crypto";
import * as FileSystem from "expo-file-system/legacy";
import type { Config } from "./config";

export type Capture = {
  id: string;
  kind: "text" | "audio";
  text: string | null;
  recording_id: string | null;
  status: "pending" | "transcribing" | "extracting" | "embedding" | "ready" | "failed";
  failure_reason: string | null;
  attempt_count: number;
  created_at: string;
};

export type Derivation = {
  id: string;
  capture_id: string;
  kind: "todo" | "event" | "idea";
  payload: Record<string, unknown> & { title?: string };
  status: "pending_review" | "confirmed" | "rejected" | "user_created";
  extractor_version: string;
  confidence: number | null;
};

export type CaptureDetail = Capture & { derivations: Derivation[] };

export type Citation = {
  chunk_id: string;
  capture_id: string;
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

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
    public retryAfter?: number,
  ) {
    super(message);
  }
}

export class Api {
  constructor(private config: Config) {}

  private async request<T>(path: string, init: RequestInit = {}): Promise<T> {
    const response = await fetch(`${this.config.baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${this.config.token}`,
        "Content-Type": "application/json",
        ...(init.headers ?? {}),
      },
    });
    if (!response.ok) {
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

  // --- me -----------------------------------------------------------------------

  getMe() {
    return this.request<{ id: string; timezone: string }>("/v1/me");
  }

  // The morning view and date extraction run in this zone, so it comes from the
  // device rather than a server-side config value.
  setTimezone(timezone: string) {
    return this.request<{ id: string; timezone: string }>("/v1/me", {
      method: "PATCH",
      body: JSON.stringify({ timezone }),
    });
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

  // --- derivations --------------------------------------------------------------

  reviewDerivation(id: string, body: { status?: "confirmed" | "rejected"; payload?: object }) {
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
    return this.config.token;
  }
}
