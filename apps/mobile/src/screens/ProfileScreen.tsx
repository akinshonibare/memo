import { Ionicons } from "@expo/vector-icons";
import * as Application from "expo-application";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Linking, Modal, Pressable, ScrollView, Switch, Text, TextInput, View } from "react-native";
import { Api, type Me, type Voice } from "../api";
import { asTranslation, TRANSLATIONS, translationName } from "../bible";
import { Avatar, IconCircle, Pill, SheetHandle } from "../components";
import { type Config, saveBaseUrl } from "../config";
import { ask, choose, confirm, Dialogs, tell } from "../dialogs";
import { fonts, useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { disableLock, enableLock, LockCover } from "../lock";
import { DEFAULT_MORNING, forgetPushToken, setMorningPush } from "../push";
import { enableReminders, LEAD_CHOICES, remindersPref } from "../reminders";
import { setCalendarSync, setRemindersSync, syncPref } from "../sync";
import { SpeechQueue } from "../voice";
import { AppearanceGroup, Group, Row } from "./SettingsParts";

type Busy =
  | "name"
  | "voice"
  | "zone"
  | "accept"
  | "export"
  | "wipe"
  | "signout"
  | "everywhere"
  | "account"
  | "server"
  | "calsync"
  | "remsync"
  | "notify"
  | "lock"
  | "faith"
  | "translation"
  | "morning";

// You first, then how memo looks, then how the assistant treats you, then what memo may
// do on this phone, then your data, then the account.
// Plumbing (the API address) is not a setting a person has: it is folded into a
// developer panel that appears after tapping the version seven times.
export function ProfileSheet({
  visible,
  config,
  lockEnabled,
  onLockChanged,
  onClose,
  onBaseUrlChanged,
  onSignOut,
  onDataDeleted,
  onProfileChanged,
  onSettingsChanged,
}: {
  visible: boolean;
  config: Config;
  lockEnabled: boolean;
  onLockChanged: (on: boolean) => void;
  onSettingsChanged: () => void;
  onClose: () => void;
  onBaseUrlChanged: (baseUrl: string) => void;
  onSignOut: () => void;
  onDataDeleted: () => void;
  onProfileChanged: (me: Me) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const signedIn = !!config.token;
  const device = deviceTimezone();
  const [me, setMe] = useState<Me | null>(null);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [spend, setSpend] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [devTaps, setDevTaps] = useState(0);
  const [baseUrl, setBaseUrl] = useState(config.baseUrl);
  const preview = useRef<SpeechQueue | null>(null);

  const api = useCallback(() => new Api(config), [config]);

  useEffect(() => {
    if (!visible || !signedIn) return;
    const client = api();
    client.getMe().then(setMe).catch(() => setMe(null));
    client.listVoices().then(setVoices).catch(() => setVoices([]));
    client
      .getUsage(30)
      .then((u) => setSpend(`$${Number(u.total_cost_usd).toFixed(2)}`))
      .catch(() => setSpend(null));
  }, [visible, signedIn, api]);

  useEffect(() => {
    if (!visible) preview.current?.stop();
  }, [visible]);

  const run = async (what: Busy, fn: () => Promise<string | null | void>) => {
    setBusy(what);
    setNote(null);
    try {
      const message = await fn();
      if (message) setNote(message);
    } catch (error) {
      setNote(`Something went wrong: ${String(error)}`);
    } finally {
      setBusy(null);
    }
  };

  const saveProfile = (what: Busy, patch: Parameters<Api["updateProfile"]>[0]) =>
    run(what, async () => {
      const updated = await api().updateProfile(patch);
      setMe(updated);
      onProfileChanged(updated);
    });

  const editName = async () => {
    const current = me?.name ?? "";
    const value = await ask({
      title: "Your name",
      message: "memo uses it when it talks to you. Leave it empty and it just says \"you\".",
      initial: current,
      allowEmpty: true,
    });
    if (value === null || value === current) return;
    void saveProfile("name", { name: value });
  };

  const voiceLabel = (id: string | null | undefined) =>
    voices.find((v) => v.id === id)?.label.split(" · ")[0] ?? "Default";

  const hear = (voiceId: string) => {
    preview.current?.stop();
    const q = new SpeechQueue((text) => api().speak(text, voiceId));
    preview.current = q;
    const who = me?.name ? `Hi ${me.name.split(" ")[0]}.` : "Hi.";
    q.enqueue(`${who} This is how I'll read your answers.`);
  };

  // Faith (ADR 0042). On the account, so the server can keep faith notes out of Home;
  // Home refreshes because what it lists just changed.
  const toggleFaith = (on: boolean) =>
    run("faith", async () => {
      const updated = await api().updateProfile({ faith_enabled: on });
      setMe(updated);
      onProfileChanged(updated);
      onSettingsChanged();
    });


  // The morning push (ADR 0047). On the account: the server sends it, at this time, only
  // on a day with something in it. Turning it on registers this phone first.
  const morningAt = me?.morning_push_at ? me.morning_push_at.slice(0, 5) : null;
  const setMorning = (time: string | null) =>
    run("morning", async () => {
      const client = api();
      const problem = await setMorningPush(client, time);
      if (problem) {
        explain(problem);
        return;
      }
      const updated = await client.getMe();
      setMe(updated);
      onProfileChanged(updated);
    });
  const pickMorning = async () => {
    const labels = MORNING_CHOICES.map((t) => `${t === morningAt ? "✓ " : ""}${morningLabel(t)}`);
    const i = await choose({ title: "Morning summary at", options: labels.map((label) => ({ label })) });
    if (i === null) return;
    const chosen = MORNING_CHOICES[i];
    if (chosen && chosen !== morningAt) void setMorning(chosen);
  };

  const pickTranslation = async () => {
    const current = asTranslation(me?.bible_translation);
    const labels = TRANSLATIONS.map((t) => `${t === current ? "✓ " : ""}${t} · ${translationName(t)}`);
    const i = await choose({
      title: "Bible translation",
      message: "Passages open in your Bible app in this translation.",
      options: labels.map((label) => ({ label })),
    });
    if (i === null) return;
    const chosen = TRANSLATIONS[i];
    if (chosen && chosen !== current) void saveProfile("translation", { bible_translation: chosen });
  };

  const pickVoice = async () => {
    if (voices.length === 0) return;
    const labels = voices.map((v) => (v.id === me?.voice ? `✓ ${v.label}` : v.label));
    const i = await choose({
      title: "Voice for spoken answers",
      message: "You'll hear a short sample.",
      options: labels.map((label) => ({ label })),
    });
    if (i === null) return;
    const chosen = voices[i];
    if (!chosen) return;
    hear(chosen.id);
    void saveProfile("voice", { voice: chosen.id });
  };

  // Everything as one JSON file, handed to the share sheet (Files, AirDrop, Mail).
  const exportData = () =>
    run("export", async () => {
      const data = await api().exportEverything();
      const day = new Date().toISOString().slice(0, 10);
      const uri = `${FileSystem.cacheDirectory}memo-export-${day}.json`;
      await FileSystem.writeAsStringAsync(uri, JSON.stringify(data, null, 2));
      await Sharing.shareAsync(uri, { UTI: "public.json", mimeType: "application/json" });
    });

  const wipeData = async () => {
    const sure = await confirm({
      title: "Delete all your data?",
      message:
        "Every note, recording, photo, item and conversation is removed for good. Your account stays. Export first if you want a copy.",
      action: "Delete all data",
      icon: "trash-outline",
    });
    if (!sure) return;
    await run("wipe", async () => {
      const client = api();
      const current = me ?? (await client.getMe());
      const result = await client.deleteEverything(current.id);
      onDataDeleted();
      const notes = result.deleted.captures ?? 0;
      return `Deleted ${notes} note${notes === 1 ? "" : "s"}.`;
    });
  };

  const signOut = () =>
    run("signout", async () => {
      try {
        // While the session still works; the server stops sending at logout anyway.
        await forgetPushToken(api());
        await api().logout();
      } finally {
        onSignOut();
      }
    });

  const signOutEverywhere = async () => {
    const sure = await confirm({
      title: "Sign out of all devices?",
      message: "Every phone signed in to memo, this one included, will need to sign in again.",
      action: "Sign out everywhere",
      icon: "log-out-outline",
    });
    if (!sure) return;
    await run("everywhere", async () => {
      await api().logoutEverywhere();
      onSignOut();
    });
  };

  // Two steps and a typed word: this one cannot be undone and takes everything.
  const deleteAccount = async () => {
    const sure = await confirm({
      title: "Delete your account?",
      message:
        "Your account and everything in it - notes, recordings, photos, items and conversations - are deleted permanently. This can't be undone.",
      action: "Continue",
      icon: "trash-outline",
    });
    if (!sure) return;
    const typed = await ask({ title: "Type DELETE to confirm", action: "Delete account" });
    if ((typed ?? "").toUpperCase() !== "DELETE") return;
    void run("account", async () => {
      const client = api();
      const current = me ?? (await client.getMe());
      await client.deleteAccount(current.id);
      onSignOut();
    });
  };

  const showShortcutHelp = async () => {
    const i = await choose({
      title: "Siri & Action Button",
      message: [
        'Siri: say "Take a note in memo" and dictate it, or "Record in memo" to start a voice note. It works right away.',
        "",
        'Action Button: Settings → Action Button → Shortcut → memo → "Record".',
        "",
        "In your own Shortcuts: memo://record, memo://write, memo://ask and memo://planner open memo where you want.",
      ].join("\n"),
      options: [{ label: "Open Shortcuts", icon: "open-outline" }],
      cancel: "Close",
    });
    if (i === 0) void Linking.openURL("shortcuts://");
  };

  // What memo may do on this phone (ADR 0034-0036). Each switch asks for its permission
  // when turned on; a refusal leaves it off and says where to change it.
  const sync = syncPref.use();
  const notify = remindersPref.use();
  // A switch that can't turn on says why in an alert, next to where you were looking,
  // not in the note at the foot of the sheet.
  const explain = (message: string | null) => {
    if (message) void tell("Not turned on", message);
  };
  const flip = (what: Busy, fn: () => Promise<string | null>) =>
    run(what, async () => {
      explain(await fn());
      onSettingsChanged();
    });
  const pickLead = async () => {
    const labels = LEAD_CHOICES.map((m) => (m === 60 ? "1 hour before" : `${m} minutes before`));
    const i = await choose({ title: "Notify me", options: labels.map((label) => ({ label })) });
    if (i === null) return;
    const minutes = LEAD_CHOICES[i];
    if (minutes !== undefined) void flip("notify", () => enableReminders(true, minutes));
  };
  const toggleLock = (on: boolean) =>
    run("lock", async () => {
      if (!on) {
        if (await disableLock()) onLockChanged(false);
        return null;
      }
      const result = await enableLock();
      if (result === "on") onLockChanged(true);
      else explain(result);
      return null;
    });

  const saveServer = () =>
    run("server", async () => {
      const next = baseUrl.trim().replace(/\/+$/, "");
      const ok = await fetch(`${next}/health`).then((r) => r.ok);
      if (!ok) throw new Error("that server didn't answer its health check");
      await saveBaseUrl(next);
      onBaseUrlChanged(next);
    });

  const version = `${Application.nativeApplicationVersion ?? "1.0.0"} (${Application.nativeBuildVersion ?? "dev"})`;
  const developer = devTaps >= 7;
  const since = me ? new Date(me.created_at).toLocaleDateString(undefined, { month: "long", year: "numeric" }) : null;

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" allowSwipeDismissal onRequestClose={onClose}>
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.top}>
          <View style={{ flex: 1 }} />
          <IconCircle name="close" size={34} onPress={onClose} />
        </View>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 56 }}>
          {signedIn && (
            <Pressable onPress={editName} style={({ pressed }) => [styles.card, pressed && styles.pressed]}>
              <Avatar name={me?.name ?? null} size={64} />
              <View style={{ flex: 1 }}>
                <Text style={[styles.name, !me?.name && { color: colors.muted }]} numberOfLines={1}>
                  {me ? (me.name ?? "Add your name") : " "}
                </Text>
                <Text style={ui.meta} numberOfLines={1}>
                  {me?.email && !me.email.endsWith("privaterelay.appleid.com") ? me.email : "Signed in with Apple"}
                </Text>
                {since && <Text style={ui.meta}>Member since {since}</Text>}
              </View>
              {busy === "name" ? (
                <ActivityIndicator color={colors.muted} />
              ) : (
                <Ionicons name="pencil" size={16} color={colors.faint} />
              )}
            </Pressable>
          )}

          <AppearanceGroup />

          {signedIn && (
            <>
              <Group title="Assistant" footer="memo calls you by this name in answers, and reads spoken answers in this voice.">
                <Row icon="person-outline" label="Name" onPress={editName} chevron busy={busy === "name"}>
                  <Text style={styles.value}>{me?.name ?? "Not set"}</Text>
                </Row>
                <Row icon="volume-high-outline" label="Voice" onPress={pickVoice} chevron busy={busy === "voice"}>
                  <Text style={styles.value}>{voiceLabel(me?.voice)}</Text>
                </Row>
              </Group>

              <Group
                title="Quick capture"
                footer="Start a voice note from the Action Button, or by asking Siri, without opening memo first."
              >
                <Row icon="flash-outline" label="Siri & Action Button" onPress={showShortcutHelp} chevron />
              </Group>

              <Group
                title="Review"
                footer="On: new todos, events and ideas are kept as soon as memo finds them, and you can still edit or remove any of them. A name that could be two people is still asked about. Off: each one waits for a tap."
              >
                <Row icon="checkmark-done-outline" label="Keep without asking" busy={busy === "accept"}>
                  <Switch
                    value={!!me?.auto_accept}
                    disabled={!me}
                    onValueChange={(on) => void saveProfile("accept", { auto_accept: on })}
                    trackColor={{ true: colors.forest }}
                  />
                </Row>
              </Group>

              <Group
                title="Faith"
                footer="A space of its own for prayers, what you're reading in the Bible, the passages your notes mention, and the notes you write there. While it's on, faith notes stay out of Home, search and Ask; turn it off and they're ordinary notes again. Passages open in your Bible app."
              >
                <Row icon="book-outline" label="Faith" busy={busy === "faith"}>
                  <Switch
                    value={!!me?.faith_enabled}
                    disabled={!me}
                    onValueChange={(on) => void toggleFaith(on)}
                    trackColor={{ true: colors.forest }}
                  />
                </Row>
                {me?.faith_enabled && (
                  <Row
                    icon="language-outline"
                    label="Bible translation"
                    onPress={pickTranslation}
                    busy={busy === "translation"}
                    chevron
                  >
                    <Text style={styles.value}>{asTranslation(me.bible_translation)}</Text>
                  </Row>
                )}
              </Group>

              <Group
                title="Morning"
                footer="A notification with today's events and the todos due today, at the time you choose. Days with nothing on send nothing. Hold it down for 👍 or 👎: that's how memo learns whether it's worth the interruption."
              >
                <Row icon="sunny-outline" label="Morning summary" busy={busy === "morning"}>
                  <Switch
                    value={!!morningAt}
                    disabled={!me}
                    onValueChange={(on) => void setMorning(on ? DEFAULT_MORNING : null)}
                    trackColor={{ true: colors.forest }}
                  />
                </Row>
                {morningAt && (
                  <Row icon="time-outline" label="Arrives at" onPress={pickMorning} chevron>
                    <Text style={styles.value}>{morningLabel(morningAt)}</Text>
                  </Row>
                )}
              </Group>

              <Group
                title="On this phone"
                footer="memo keeps its events in a calendar called memo, and its todos in a Reminders list called memo. Tick a todo in either app and it's ticked in both. Turning one off removes memo's calendar or list from the phone. memo never reads your own events."
              >
                <Row icon="calendar-outline" label="Put events in Calendar" busy={busy === "calsync"}>
                  <Switch
                    value={sync.calendar}
                    onValueChange={(on) => void flip("calsync", () => setCalendarSync(on))}
                    trackColor={{ true: colors.forest }}
                  />
                </Row>
                <Row icon="list-outline" label="Put todos in Reminders" busy={busy === "remsync"}>
                  <Switch
                    value={sync.reminders}
                    onValueChange={(on) => void flip("remsync", () => setRemindersSync(on))}
                    trackColor={{ true: colors.forest }}
                  />
                </Row>
                <Row icon="notifications-outline" label="Remind me before events" busy={busy === "notify"}>
                  <Switch
                    value={notify.on}
                    onValueChange={(on) => void flip("notify", () => enableReminders(on, notify.minutes))}
                    trackColor={{ true: colors.forest }}
                  />
                </Row>
                {notify.on && (
                  <Row icon="time-outline" label="Notify me" onPress={pickLead} chevron>
                    <Text style={styles.value}>
                      {notify.minutes === 60 ? "1 hour before" : `${notify.minutes} min before`}
                    </Text>
                  </Row>
                )}
              </Group>

              <Group
                title="Privacy"
                footer="Asks for Face ID when memo opens, and after it's been away for a minute. The app switcher shows nothing while it's locked."
              >
                <Row icon="lock-closed-outline" label="Lock with Face ID" busy={busy === "lock"}>
                  <Switch value={lockEnabled} onValueChange={(on) => void toggleLock(on)} trackColor={{ true: colors.forest }} />
                </Row>
              </Group>

              <Group title="Preferences" footer="Your morning view is built at 04:00 in this timezone.">
                <Row icon="globe-outline" label="Timezone" busy={busy === "zone"}>
                  {me && me.timezone !== device ? (
                    <Pill label={`Use ${device}`} onPress={() => void saveProfile("zone", { timezone: device })} />
                  ) : (
                    <Text style={styles.value}>{me?.timezone ?? ""}</Text>
                  )}
                </Row>
              </Group>

              <Group title="Usage" footer="What memo's AI calls - extraction, answers, photo descriptions - have cost.">
                <Row icon="stats-chart-outline" label="Last 30 days">
                  <Text style={styles.value}>{spend ?? "…"}</Text>
                </Row>
              </Group>

              <Group title="Your data">
                <Row icon="share-outline" label="Export everything" onPress={exportData} chevron busy={busy === "export"} />
                <Row icon="trash-outline" label="Delete all data" danger onPress={wipeData} busy={busy === "wipe"} />
              </Group>

              <Group title="Account">
                <Row icon="log-out-outline" label="Sign out" onPress={signOut} busy={busy === "signout"} />
                <Row
                  icon="phone-portrait-outline"
                  label="Sign out of all devices"
                  onPress={signOutEverywhere}
                  busy={busy === "everywhere"}
                />
                <Row icon="close-circle-outline" label="Delete account" danger onPress={deleteAccount} busy={busy === "account"} />
              </Group>
            </>
          )}

          {developer && (
            <Group title="Developer">
              <View style={styles.serverEdit}>
                <TextInput
                  style={styles.input}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="url"
                  value={baseUrl}
                  onChangeText={setBaseUrl}
                  placeholderTextColor={colors.muted}
                />
                <Pill
                  label="Use this server"
                  tone="light"
                  onPress={saveServer}
                  busy={busy === "server"}
                  disabled={!baseUrl.trim() || baseUrl.trim() === config.baseUrl}
                />
              </View>
            </Group>
          )}

          {note && <Text style={[ui.meta, styles.note]}>{note}</Text>}
          <Pressable onPress={() => setDevTaps((n) => n + 1)} style={styles.footer}>
            <Text style={styles.brand}>memo</Text>
            <Text style={ui.meta}>Version {version}</Text>
          </Pressable>
        </ScrollView>
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

const MORNING_CHOICES = ["06:00", "06:30", "07:00", "07:30", "08:00", "08:30", "09:00", "09:30"];

// "07:30" -> "7:30am", the way the push itself writes times.
function morningLabel(time: string): string {
  const [h = 0, m = 0] = time.split(":").map(Number);
  const hour = h % 12 || 12;
  return `${hour}${m ? `:${String(m).padStart(2, "0")}` : ""}${h < 12 ? "am" : "pm"}`;
}

function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const useStyles = makeStyles((colors) => ({
  sheet: { flex: 1, backgroundColor: colors.bg, paddingHorizontal: 20 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  pressed: { opacity: 0.6 },
  card: {
    flexDirection: "row",
    alignItems: "center",
    gap: 14,
    marginTop: 8,
    padding: 16,
    borderRadius: 18,
    backgroundColor: colors.surface,
  },
  name: { color: colors.text, fontSize: 20, fontWeight: "600", marginBottom: 2 },
  value: { color: colors.muted, fontSize: 15, flexShrink: 1 },
  serverEdit: { padding: 14, gap: 10, alignItems: "flex-end" },
  input: {
    alignSelf: "stretch",
    fontSize: 15,
    color: colors.text,
    paddingHorizontal: 14,
    paddingVertical: 11,
    borderRadius: 10,
    backgroundColor: colors.surfaceAlt,
  },
  note: { marginTop: 20, textAlign: "center" },
  footer: { alignItems: "center", marginTop: 36, gap: 2 },
  brand: { fontFamily: fonts.serif, fontSize: 22, color: colors.muted },
}));
