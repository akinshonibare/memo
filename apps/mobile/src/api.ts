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
  space?: Space; // ADR 0042; absent from an older server
};

// Where a note lives (ADR 0042): with the rest, or in Faith.
export type Space = "main" | "faith";

export type Derivation = {
  id: string;
  // null once the note it came from is deleted: the item is kept (ADR 0022).
  capture_id: string | null;
  kind: "todo" | "event" | "idea";
  payload: Record<string, unknown> & { title?: string };
  // accepted: kept without a look because auto-accept is on (ADR 0030).
  status: "pending_review" | "confirmed" | "rejected" | "user_created" | "accepted";
  extractor_version: string;
  confidence: number | null;
  completed_at: string | null; // todos only: when it was ticked off
  created_at: string;
};

// --- people (ADR 0029) ----------------------------------------------------------

export type Contact = "in_person" | "call" | "message" | "unspecified" | "none";

export type PersonRef = { id: string; name: string; note: string | null; last_contact_on: string | null };

// A person in a note. `person` is null while memo waits for you to say who it is;
// `candidates` are who it could be.
export type Mention = {
  id: string;
  capture_id: string | null;
  status: Derivation["status"];
  confidence: number | null;
  name: string;
  full_name: string | null;
  hint: string | null;
  contact: Contact;
  occurred_on: string | null;
  context: string | null;
  person: PersonRef | null;
  candidates: PersonRef[];
  created_at: string;
  written_at: string | null;
  excerpt: string | null;
};

export type Person = {
  id: string;
  name: string;
  aliases: string[];
  note: string | null;
  reach_out_every_days: number | null; // null: never remind
  snoozed_until: string | null;
  last_contact_on: string | null;
  mention_count: number;
  due: boolean;
  created_at: string;
  family?: boolean; // for the "time with family" habit (ADR 0043); absent from an older server
  // Lately (ADR 0062): a line on what's been going on with them, written ahead by the
  // server from their newest notes. Null until written, or when the notes say nothing.
  lately?: string | null;
  lately_at?: string | null;
};

export type PersonDetail = Person & {
  mentions: Mention[];
  open_todos: Derivation[];
  upcoming_events: Derivation[];
  prayers?: Prayer[]; // only while Faith is on (ADR 0042)
};

export type PeopleList = {
  people: Person[];
  unresolved: number;
  unread_notes: number; // older notes not read for people yet
  unreadable_notes?: number; // ones the server gave up on after repeated failures
};

export type ReachOut = {
  person_id: string;
  name: string;
  last_contact_on: string;
  every_days: number;
  latest: string | null;
  latest_on: string | null;
};

// `words`: when each word of a voice note was said, [start_ms, end_ms, word] (ADR 0039),
// for following the transcript as it plays. Absent from an older server, null for a
// note transcribed before words were kept.
export type Word = [number, number, string];
export type CaptureDetail = Capture & {
  derivations: Derivation[];
  people?: Mention[];
  words?: Word[] | null;
  passages?: Passage[]; // the Bible passages a faith note is about (ADR 0042)
};

// --- Faith (ADR 0042) ---------------------------------------------------------------

export type Passage = {
  ref: string; // "Romans 8:28–30"
  book: string; // USFM: ROM
  chapter: number;
  verse: number | null;
  end_chapter: number;
  end_verse: number | null;
};

// A passage you read, on a day.
export type ReadingLog = { id: string; passage: Passage; on: string };

// What you're reading, as you set it; there is no fixed plan (ADR 0042, revised).
export type ReadingState = {
  current: Passage | null; // "John", "Romans 8"; null until you set something
  next: Passage | null; // the chapter after the last one you read in it
  last: ReadingLog | null; // what Undo takes back
  recent: ReadingLog[];
};

// One passage your notes talk about, and the notes that do.
export type PassageRef = {
  passage: Passage;
  count: number;
  last_on: string;
  notes: { capture_id: string; on: string; excerpt: string | null }[];
};

export type Prayer = {
  id: string;
  title: string;
  notes: string | null;
  answer: string | null; // how it was answered
  person_id: string | null;
  person_name: string | null;
  capture_id: string | null;
  created_at: string;
  answered_at: string | null;
};

export type FaithMoment = {
  kind: "prayer" | "passage";
  prayer: Prayer | null;
  on: string; // the day it was prayed, or written about
  passage: Passage | null;
  capture_id: string | null;
  excerpt: string | null;
};

export type Faith = {
  enabled: boolean;
  translation: string;
  moment: FaithMoment | null;
  // Absent from a server older than what-you-read (ADR 0042, revised).
  reading?: ReadingState;
  passages?: PassageRef[];
  open: Prayer[];
  answered: Prayer[];
  // How you've sounded this week (ADR 0058). Null while Within is off; absent from a
  // server older than mood.
  mood?: MoodWeek | null;
};

// --- Mood, in Within (ADR 0058) -----------------------------------------------------

// Five steps, low to bright. memo's read of a note is a guess; your own word for a day
// (`yours`) outranks it.
export type Valence = -2 | -1 | 0 | 1 | 2;

export type MoodDay = {
  day: string; // YYYY-MM-DD, your local day
  valence: Valence | null; // null: nothing said that day
  yours: boolean;
  words: string[];
  notes: number;
};

export type MoodWeek = { days: MoodDay[]; summary: string | null };

export type MoodRead = {
  capture_id: string;
  title: string;
  created_at: string;
  valence: Valence;
  words: string[];
  evidence: string | null;
};

export type MoodDayDetail = { day: MoodDay; reads: MoodRead[] };

// --- Habits (ADR 0043) --------------------------------------------------------------

export type Habit = {
  id: string;
  key: string;
  name: string;
  source: "activity" | "family" | "bible";
  target_per_week: number | null;
  this_week: number; // days, Monday to Sunday
  week_days: boolean[]; // this week, Monday first
  last_done: string | null;
  total_days: number;
  since: string | null;
  weeks: number[]; // the last 8 weeks' days, oldest first
};

export type HabitSuggestion = {
  key: string;
  name: string;
  source: Habit["source"];
  days: number; // different days in the last 21
  total_days: number;
};

export type Habits = { enabled: boolean; habits: Habit[]; suggestion: HabitSuggestion | null };

export type HabitEvidence = {
  kind: "note" | "logged" | "contact" | "reading" | "passage" | "health";
  text: string | null;
  capture_id: string | null;
  activity_id: string | null;
};

export type HabitDetail = Habit & {
  days: { date: string; evidence: HabitEvidence[]; removable: boolean }[];
  milestones?: Milestone[]; // newest first; missing from a server older than ADR 0050
};

// Something a habit reached, noticed rather than logged (ADR 0050).
export type Milestone = {
  kind: "days" | "first" | "longest" | "heaviest";
  title: string; // "First 10k", "Heaviest squat yet", "10th day"
  detail: string; // "10.2 km", "100 kg", "Gym"
  date: string;
  capture_id: string | null;
};

// Today's milestone line: one from the last couple of days, for a habit you track.
export type TodayMilestone = Milestone & { habit_id: string; name: string };

// A workout read from Apple Health, as PUT /v1/workouts takes it (ADR 0050).
export type WorkoutIn = {
  id: string;
  activity_type: number;
  start: string;
  end: string;
  duration_s: number;
  distance_m: number | null;
};

// Today's one habit line: a suggestion, or a nudge for a habit gone quiet.
export type HabitPrompt = {
  kind: "suggest" | "nudge";
  key: string;
  name: string;
  source: Habit["source"];
  habit_id: string | null;
  days: number;
  last_done: string | null;
};

export type HabitWeek = { habit_id: string; name: string; days: number; target_per_week: number | null };

export type FaithWeek = {
  read?: ReadingLog[];
  answered: Prayer[];
  passages: { passage: Passage; capture_id: string | null }[];
};

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

// GET /v1/news: the newest thing in each of memo's places; null where there's none.
export type News = Record<"planner" | "review" | "people" | "faith" | "habits", string | null>;

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
  reach_out?: ReachOut[]; // absent from a server older than ADR 0029
  // A note from this day a month, six months or years ago (ADR 0040). Absent from an
  // older server; null on a day with nothing to look back on.
  on_this_day?: OnThisDay | null;
  habit?: HabitPrompt | null; // ADR 0043; only while Habits is on
  milestone?: TodayMilestone | null; // ADR 0050; reached in the last couple of days
};

export type OnThisDay = {
  capture_id: string;
  kind: Capture["kind"];
  text: string;
  captured_on: string;
};

export type Once = { clientToken: string; capturedAt: string };

export function newOnce(): Once {
  return { clientToken: Crypto.randomUUID(), capturedAt: new Date().toISOString() };
}

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
  auto_accept?: boolean; // keep what extraction finds without asking (ADR 0030)
  created_at: string;
  note_count?: number; // for milestones; absent from an older server
  faith_enabled?: boolean; // ADR 0042; off by default
  bible_translation?: string; // what Bible links open in; NIV by default
  habits_enabled?: boolean; // always true since ADR 0054, unless an operator turned it off
  morning_push_at?: string | null; // "07:30:00", or null for no morning push (ADR 0047)
  tone?: Tone; // how memo writes for you (ADR 0061); absent from an older server
};

// How memo writes answers, highlight summaries and photo descriptions (ADR 0061).
export type Tone = "plain" | "warm" | "brief" | "playful";

export type Voice = { id: string; label: string };

// A later note that may answer a prayer or carry on an idea (ADR 0048). A question
// until you say yes or no; nothing changes before then.
export type Update = {
  id: string;
  relation: "answers" | "continues";
  quote: string; // the note's own words that suggest it
  status: "pending_review" | "confirmed" | "rejected";
  capture_id: string | null;
  written_at: string;
  target: {
    id: string;
    kind: "prayer" | "idea";
    title: string;
    summary: string | null;
    person_name: string | null;
    answered_at: string | null;
  };
};

export type Usage = { window_days: number; total_cost_usd: string };

// Today's AI spend against the daily limit (ADR 0063). limit_usd null: no limit.
export type UsageToday = { spent_usd: string; limit_usd: string | null; over: boolean; resets_at: string };

export type SearchHit = { snippet: string; capture: Capture | null; derivation: Derivation | null };

export type Week = {
  start: string;
  end: string;
  captured: { notes: number; voice: number; photos: number };
  done: Derivation[];
  still_open: Derivation[];
  still_open_total: number;
  coming_up: Derivation[];
  new_ideas: Derivation[];
  faith?: FaithWeek | null; // only while Faith is on
  habits?: HabitWeek[] | null; // only while Habits is on
};

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

  updateProfile(patch: {
    name?: string;
    voice?: string | null;
    timezone?: string;
    auto_accept?: boolean;
    faith_enabled?: boolean;
    bible_translation?: string;
    morning_push_at?: string | null;
    tone?: Tone;
  }) {
    return this.request<Me>("/v1/me", { method: "PATCH", body: JSON.stringify(patch) });
  }

  // --- what's new in each place (Home's Places button) --------------------------

  getNews() {
    return this.request<News>("/v1/news");
  }

  // --- updates (ADR 0048) --------------------------------------------------------

  listUpdates(filter: { status?: "pending" | "confirmed"; kind?: "prayer" | "idea" } = {}) {
    const params = new URLSearchParams();
    if (filter.status) params.set("status", filter.status);
    if (filter.kind) params.set("kind", filter.kind);
    const query = params.toString();
    return this.request<Update[]>(`/v1/updates${query ? `?${query}` : ""}`);
  }

  answerUpdate(id: string, decision: "yes" | "no") {
    return this.request<Update>(`/v1/updates/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ decision }),
    });
  }

  // --- push (ADR 0047) -----------------------------------------------------------

  registerPushToken(token: string) {
    return this.request<void>("/v1/push/token", { method: "PUT", body: JSON.stringify({ token }) });
  }

  forgetPushToken(token: string) {
    return this.request<void>("/v1/push/token", { method: "DELETE", body: JSON.stringify({ token }) });
  }

  // --- Apple Health (ADR 0050) -----------------------------------------------------

  syncWorkouts(since: string, until: string, workouts: WorkoutIn[]) {
    return this.request<{ filed: number; skipped: number; removed: number }>("/v1/workouts", {
      method: "PUT",
      body: JSON.stringify({ since, until, workouts }),
    });
  }

  forgetWorkouts() {
    return this.request<void>("/v1/workouts", { method: "DELETE" });
  }

  pushOpened(pushId: string) {
    return this.request<void>(`/v1/pushes/${pushId}/opened`, { method: "POST" });
  }

  pushFeedback(pushId: string, vote: "up" | "down") {
    return this.request<void>(`/v1/pushes/${pushId}/feedback`, {
      method: "POST",
      body: JSON.stringify({ vote }),
    });
  }

  listVoices() {
    return this.request<Voice[]>("/v1/me/voices");
  }

  getUsage(days: number) {
    return this.request<Usage>(`/v1/usage?days=${days}`);
  }

  getUsageToday() {
    return this.request<UsageToday>("/v1/usage/today");
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

  // `once` identifies a capture across retries (the server's idempotency key) and
  // carries the time it was actually taken, for one synced later from the outbox.
  // `space` is sent only for a faith note: an ordinary note's request is unchanged.
  createTextCapture(text: string, once: Once = newOnce(), space: Space = "main") {
    return this.request<Capture>("/v1/captures", {
      method: "POST",
      body: JSON.stringify({
        text,
        client_token: once.clientToken,
        captured_at: once.capturedAt,
        ...(space === "faith" ? { space } : {}),
      }),
    });
  }

  createAudioCapture(
    fileUri: string,
    mime = "audio/m4a",
    durationMs?: number,
    once: Once = newOnce(),
    space: Space = "main",
  ) {
    return this.uploadCapture(fileUri, mime, once, durationMs, space);
  }

  // A photo (ADR 0024): resized JPEG, uploaded like a recording, described on the server.
  // `caption`: your own words with the photo (ADR 0056), kept first in the note.
  createImageCapture(fileUri: string, once: Once = newOnce(), space: Space = "main", caption?: string) {
    return this.uploadCapture(fileUri, "image/jpeg", once, undefined, space, caption);
  }

  private async uploadCapture(
    fileUri: string,
    mime: string,
    once: Once,
    durationMs?: number,
    space: Space = "main",
    caption?: string,
  ) {
    const presigned = await this.request<{ recording_id: string; upload_url: string }>("/v1/recordings", {
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
        client_token: once.clientToken,
        captured_at: once.capturedAt,
        ...(space === "faith" ? { space } : {}),
        ...(caption ? { caption } : {}),
      }),
    });
  }

  // Meaning search over everything (ADR 0028), the same retrieval Ask uses.
  // The query goes in the body: it is a piece of your notes, and URLs get logged.
  search(q: string) {
    return this.request<SearchHit[]>("/v1/search", {
      method: "POST",
      body: JSON.stringify({ q, limit: 20 }),
    });
  }

  related(captureId: string) {
    return this.request<{ capture: Capture; similarity: number }[]>(`/v1/captures/${captureId}/related`);
  }

  weekReview(weeksAgo = 0) {
    return this.request<Week>(`/v1/review/week?weeks_ago=${weeksAgo}`);
  }

  listCaptures(limit = 50, space?: Space) {
    return this.request<{ items: Capture[]; next_cursor: string | null }>(
      `/v1/captures?limit=${limit}${space ? `&space=${space}` : ""}`,
    );
  }

  // Into Faith, or back out (ADR 0042).
  moveCapture(id: string, space: Space) {
    return this.request<Capture>(`/v1/captures/${id}/move`, {
      method: "POST",
      body: JSON.stringify({ space }),
    });
  }

  // --- Faith (ADR 0042) ---------------------------------------------------------

  getFaith() {
    return this.request<Faith>("/v1/faith");
  }

  createPrayer(body: { title: string; notes?: string | null; person_id?: string | null }) {
    return this.request<Prayer>("/v1/faith/prayers", { method: "POST", body: JSON.stringify(body) });
  }

  updatePrayer(
    id: string,
    patch: {
      title?: string;
      notes?: string | null;
      answer?: string | null;
      person_id?: string | null;
      answered?: boolean;
      removed?: true;
    },
  ) {
    return this.request<Prayer>(`/v1/faith/prayers/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }

  // What you're reading: "John", "Psalms", "Romans 8". null clears it.
  setReading(ref: string | null) {
    return this.request<ReadingState>("/v1/faith/reading", { method: "PUT", body: JSON.stringify({ ref }) });
  }

  // One day's mood: the reads behind it, the phrase from each, and your word if given.
  moodDay(day: string) {
    return this.request<MoodDayDetail>(`/v1/faith/mood/${day}`);
  }

  // Your word for a day, which outranks memo's reads of it; null takes it back.
  sayMood(day: string, valence: Valence | null) {
    return valence === null
      ? this.request<MoodDayDetail>(`/v1/faith/mood/${day}`, { method: "DELETE" })
      : this.request<MoodDayDetail>(`/v1/faith/mood/${day}`, { method: "PUT", body: JSON.stringify({ valence }) });
  }

  // You read something: what you typed, or else the next chapter of what you're reading.
  logReading(ref?: string) {
    return this.request<ReadingState>("/v1/faith/readings", {
      method: "POST",
      body: JSON.stringify(ref ? { ref } : {}),
    });
  }

  undoReading(id: string) {
    return this.request<ReadingState>(`/v1/faith/readings/${id}`, { method: "DELETE" });
  }

  // --- Habits (ADR 0043) --------------------------------------------------------

  getHabits() {
    return this.request<Habits>("/v1/habits");
  }

  // A suggestion accepted (pass its key), or a habit you name.
  trackHabit(body: { name: string; key?: string; target_per_week?: number | null }) {
    return this.request<Habit>("/v1/habits", { method: "POST", body: JSON.stringify(body) });
  }

  dismissHabit(key: string) {
    return this.request<void>("/v1/habits/dismiss", { method: "POST", body: JSON.stringify({ key }) });
  }

  getHabit(id: string) {
    return this.request<HabitDetail>(`/v1/habits/${id}`);
  }

  updateHabit(id: string, patch: { name?: string; target_per_week?: number | null }) {
    return this.request<Habit>(`/v1/habits/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }

  stopHabit(id: string) {
    return this.request<void>(`/v1/habits/${id}`, { method: "DELETE" });
  }

  logHabit(id: string, date?: string) {
    return this.request<Habit>(`/v1/habits/${id}/log`, {
      method: "POST",
      body: JSON.stringify(date ? { date } : {}),
    });
  }

  removeHabitDay(id: string, date: string) {
    return this.request<Habit>(`/v1/habits/${id}/days/${date}`, { method: "DELETE" });
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
    return (await this.derivationPages(max)).items;
  }

  // The same, saying whether it stopped at `max` with older items left unread: a sync
  // must not take "not in this list" to mean "deleted" when the list was cut short.
  async derivationPages(max: number): Promise<{ items: Derivation[]; truncated: boolean }> {
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
    return { items, truncated: cursor !== null };
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

  // --- people (ADR 0029) -------------------------------------------------------

  listPeople() {
    return this.request<PeopleList>("/v1/people");
  }

  // Who's who: every mention waiting for "which one?".
  unresolvedMentions() {
    return this.request<Mention[]>("/v1/people/unresolved");
  }

  getPerson(id: string) {
    return this.request<PersonDetail>(`/v1/people/${id}`);
  }

  updatePerson(
    id: string,
    patch: {
      name?: string;
      aliases?: string[];
      note?: string | null;
      reach_out_every_days?: number | null;
      snoozed_until?: string | null;
      family?: boolean | null; // null: back to memo's guess
    },
  ) {
    return this.request<Person>(`/v1/people/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  }

  // "Spoke to them": today, unless another day is given.
  logContact(id: string, occurredOn?: string) {
    return this.request<Person>(`/v1/people/${id}/contact`, {
      method: "POST",
      body: JSON.stringify(occurredOn ? { occurred_on: occurredOn } : {}),
    });
  }

  // Forget someone: the person and every mention of them go; the notes stay (ADR 0055).
  deletePerson(id: string) {
    return this.request<void>(`/v1/people/${id}`, { method: "DELETE" });
  }

  // `id` and `into` are one person: `id`'s notes move to `into`, and `id` is gone.
  mergePerson(id: string, into: string) {
    return this.request<PersonDetail>(`/v1/people/${id}/merge`, {
      method: "POST",
      body: JSON.stringify({ into }),
    });
  }

  // Answer "who is this?": a person, someone new, or confirm / reject as it stands.
  resolveMention(
    id: string,
    answer: { person_id: string } | { new_person: true } | { status: "confirmed" | "rejected" },
  ) {
    return this.request<Mention>(`/v1/mentions/${id}`, { method: "PATCH", body: JSON.stringify(answer) });
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
