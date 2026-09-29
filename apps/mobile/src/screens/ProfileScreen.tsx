import { Ionicons } from "@expo/vector-icons";
import * as Application from "expo-application";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import {
  ActionSheetIOS,
  ActivityIndicator,
  Alert,
  Linking,
  Modal,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Api, type Me, type Voice } from "../api";
import { Avatar, IconCircle, Pill, SheetHandle } from "../components";
import { type Config, saveBaseUrl } from "../config";
import { colors, fonts, ui } from "../ui";
import { SpeechQueue } from "../voice";

type Busy = "name" | "voice" | "zone" | "export" | "wipe" | "signout" | "everywhere" | "account" | "server";

// You first, then how the assistant treats you, then your data, then the account.
// Plumbing (the API address) is not a setting a person has: it is folded into a
// developer panel that appears after tapping the version seven times.
export function ProfileSheet({
  visible,
  config,
  onClose,
  onBaseUrlChanged,
  onSignOut,
  onDataDeleted,
  onProfileChanged,
}: {
  visible: boolean;
  config: Config;
  onClose: () => void;
  onBaseUrlChanged: (baseUrl: string) => void;
  onSignOut: () => void;
  onDataDeleted: () => void;
  onProfileChanged: (me: Me) => void;
}) {
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

  const editName = () => {
    const current = me?.name ?? "";
    const apply = (value: string | undefined) => {
      if (value === undefined || value.trim() === current) return;
      void saveProfile("name", { name: value.trim() });
    };
    if (Platform.OS === "ios") {
      Alert.prompt(
        "Your name",
        "memo uses it when it talks to you. Leave it empty and it just says \"you\".",
        apply,
        "plain-text",
        current,
      );
    } else {
      Alert.alert("Your name", "Editing the name needs iOS for now.");
    }
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

  const pickVoice = () => {
    if (voices.length === 0) return;
    const labels = voices.map((v) => (v.id === me?.voice ? `✓ ${v.label}` : v.label));
    const choose = (i: number) => {
      const chosen = voices[i];
      if (!chosen) return;
      hear(chosen.id);
      void saveProfile("voice", { voice: chosen.id });
    };
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions(
        {
          title: "Voice for spoken answers",
          message: "You'll hear a short sample.",
          options: [...labels, "Cancel"],
          cancelButtonIndex: labels.length,
        },
        choose,
      );
      return;
    }
    Alert.alert("Voice", undefined, [
      ...labels.map((text, i) => ({ text, onPress: () => choose(i) })),
      { text: "Cancel", style: "cancel" as const },
    ]);
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

  const wipeData = () =>
    Alert.alert(
      "Delete all your data?",
      "Every note, recording, photo, item and conversation is removed for good. Your account stays. Export first if you want a copy.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete all data",
          style: "destructive",
          onPress: () =>
            run("wipe", async () => {
              const client = api();
              const current = me ?? (await client.getMe());
              const result = await client.deleteEverything(current.id);
              onDataDeleted();
              const notes = result.deleted.captures ?? 0;
              return `Deleted ${notes} note${notes === 1 ? "" : "s"}.`;
            }),
        },
      ],
    );

  const signOut = () =>
    run("signout", async () => {
      try {
        await api().logout();
      } finally {
        onSignOut();
      }
    });

  const signOutEverywhere = () =>
    Alert.alert("Sign out of all devices?", "Every phone signed in to memo, this one included, will need to sign in again.", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Sign out everywhere",
        style: "destructive",
        onPress: () =>
          run("everywhere", async () => {
            await api().logoutEverywhere();
            onSignOut();
          }),
      },
    ]);

  // Two steps and a typed word: this one cannot be undone and takes everything.
  const deleteAccount = () =>
    Alert.alert(
      "Delete your account?",
      "Your account and everything in it - notes, recordings, photos, items and conversations - are deleted permanently. This can't be undone.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Continue",
          style: "destructive",
          onPress: () =>
            Alert.prompt(
              "Type DELETE to confirm",
              undefined,
              (typed) => {
                if ((typed ?? "").trim().toUpperCase() !== "DELETE") return;
                void run("account", async () => {
                  const client = api();
                  const current = me ?? (await client.getMe());
                  await client.deleteAccount(current.id);
                  onSignOut();
                });
              },
              "plain-text",
            ),
        },
      ],
    );

  const showShortcutHelp = () =>
    Alert.alert(
      "Siri & Action Button",
      [
        'Siri: say "Take a note in memo" and dictate it, or "Record in memo" to start a voice note. It works right away.',
        "",
        'Action Button: Settings → Action Button → Shortcut → memo → "Record".',
        "",
        "In your own Shortcuts: memo://record, memo://write, memo://ask and memo://planner open memo where you want.",
      ].join("\n"),
      [
        { text: "Close", style: "cancel" },
        { text: "Open Shortcuts", onPress: () => void Linking.openURL("shortcuts://") },
      ],
    );

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
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
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
      </View>
    </Modal>
  );
}

function Group({ title, footer, children }: { title: string; footer?: string; children: ReactNode }) {
  return (
    <View style={styles.group}>
      <Text style={ui.label}>{title}</Text>
      <View style={styles.groupCard}>{children}</View>
      {footer && <Text style={[ui.meta, styles.groupFooter]}>{footer}</Text>}
    </View>
  );
}

function Row({
  label,
  icon,
  onPress,
  danger,
  busy,
  chevron,
  children,
}: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  onPress?: () => void;
  danger?: boolean;
  busy?: boolean;
  chevron?: boolean;
  children?: ReactNode;
}) {
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress || busy}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <Ionicons name={icon} size={19} color={danger ? colors.danger : colors.muted} />
      <Text style={[styles.rowLabel, danger && { color: colors.danger }]} numberOfLines={1}>
        {label}
      </Text>
      <View style={styles.rowRight}>
        {busy ? <ActivityIndicator color={colors.muted} /> : children}
        {chevron && !busy && <Ionicons name="chevron-forward" size={16} color={colors.faint} />}
      </View>
    </Pressable>
  );
}

function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

const styles = StyleSheet.create({
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
  group: { marginTop: 26 },
  groupCard: { backgroundColor: colors.surface, borderRadius: 14, overflow: "hidden" },
  groupFooter: { marginTop: 6, marginHorizontal: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 48,
    paddingHorizontal: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  rowLabel: { fontSize: 16, color: colors.text, flexShrink: 1 },
  rowRight: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 8 },
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
});
