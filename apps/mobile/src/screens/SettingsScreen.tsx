import { Ionicons } from "@expo/vector-icons";
import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { type ReactNode, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import { Api } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { type Config, saveBaseUrl } from "../config";
import { colors, ui } from "../ui";

// Grouped rows, iOS-style. The server URL is a developer setting, so it sits folded
// under Advanced; the timezone is read from the device rather than typed, because a
// typo there silently moves the 04:00 morning build.
export function SettingsSheet({
  visible,
  config,
  onClose,
  onBaseUrlChanged,
  onSignOut,
  onDataDeleted,
}: {
  visible: boolean;
  config: Config;
  onClose: () => void;
  onBaseUrlChanged: (baseUrl: string) => void;
  onSignOut: () => void;
  onDataDeleted: () => void;
}) {
  const signedIn = !!config.token;
  const device = deviceTimezone();
  const [serverZone, setServerZone] = useState<string | null>(null);
  const [busy, setBusy] = useState<"zone" | "signout" | "export" | "delete" | "server" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const [advanced, setAdvanced] = useState(false);
  const [baseUrl, setBaseUrl] = useState(config.baseUrl);

  useEffect(() => {
    if (!visible || !signedIn) return;
    new Api(config)
      .getMe()
      .then((me) => setServerZone(me.timezone))
      .catch(() => setServerZone(null));
  }, [visible, signedIn, config]);

  const run = async (what: NonNullable<typeof busy>, fn: () => Promise<string | null>) => {
    setBusy(what);
    setNote(null);
    try {
      setNote(await fn());
    } catch (error) {
      setNote(`Something went wrong: ${String(error)}`);
    } finally {
      setBusy(null);
    }
  };

  const useDeviceZone = () =>
    run("zone", async () => {
      const me = await new Api(config).setTimezone(device);
      setServerZone(me.timezone);
      return null;
    });

  const signOut = async () => {
    setBusy("signout");
    try {
      await new Api(config).logout();
    } catch {
      // The session is being dropped locally either way.
    } finally {
      setBusy(null);
      onSignOut();
    }
  };

  // Everything as one JSON file, handed to the share sheet (Files, AirDrop, Mail).
  // Recordings are download links inside it that expire, not bytes.
  const exportData = () =>
    run("export", async () => {
      const data = await new Api(config).exportEverything();
      const day = new Date().toISOString().slice(0, 10);
      const uri = `${FileSystem.cacheDirectory}memo-export-${day}.json`;
      await FileSystem.writeAsStringAsync(uri, JSON.stringify(data, null, 2));
      await Sharing.shareAsync(uri, { UTI: "public.json", mimeType: "application/json" });
      return null;
    });

  const deleteData = () =>
    Alert.alert(
      "Delete everything?",
      "Every note, recording, item and conversation is removed for good. You stay signed in. Export first if you want a copy.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete everything",
          style: "destructive",
          onPress: () =>
            run("delete", async () => {
              const api = new Api(config);
              const me = await api.getMe();
              const result = await api.deleteEverything(me.id);
              onDataDeleted();
              const notes = result.deleted.captures ?? 0;
              return `Deleted ${notes} note${notes === 1 ? "" : "s"}.`;
            }),
        },
      ],
    );

  const saveServer = () =>
    run("server", async () => {
      const next = baseUrl.trim().replace(/\/+$/, "");
      const ok = await fetch(`${next}/health`).then((r) => r.ok);
      if (!ok) throw new Error("that server didn't answer its health check");
      await saveBaseUrl(next);
      onBaseUrlChanged(next);
      return null;
    });

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.top}>
          <Text style={ui.h2}>Settings</Text>
          <View style={{ flex: 1 }} />
          <IconCircle name="close" size={36} onPress={onClose} />
        </View>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 48 }}>
          {signedIn && (
            <>
              <Group title="Timezone" footer="Your morning view is built at 04:00 in this zone.">
                <Row label={serverZone ?? "…"} icon="globe-outline">
                  {serverZone && serverZone !== device ? (
                    <Pill label={`Use ${device}`} onPress={useDeviceZone} busy={busy === "zone"} />
                  ) : (
                    <Text style={styles.value}>{serverZone ? "Matches this phone" : ""}</Text>
                  )}
                </Row>
              </Group>

              <Group title="Your data">
                <Row label="Export everything" icon="share-outline" onPress={exportData} busy={busy === "export"} chevron />
                <Row label="Delete everything" icon="trash-outline" danger onPress={deleteData} busy={busy === "delete"} />
              </Group>

              <Group title="Account">
                <Row label="Sign out" icon="log-out-outline" danger onPress={signOut} busy={busy === "signout"} />
              </Group>
            </>
          )}

          <Group title="Advanced">
            <Row
              label="Server"
              icon="server-outline"
              onPress={() => setAdvanced((a) => !a)}
              chevron={advanced ? "up" : "down"}
            >
              <Text style={styles.value} numberOfLines={1}>
                {hostOf(config.baseUrl)}
              </Text>
            </Row>
            {advanced && (
              <View style={styles.serverEdit}>
                <TextInput
                  style={styles.input}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="url"
                  value={baseUrl}
                  onChangeText={setBaseUrl}
                  placeholder="https://api-production-xxxx.up.railway.app"
                  placeholderTextColor={colors.muted}
                />
                <Pill
                  label="Save"
                  tone="light"
                  onPress={saveServer}
                  busy={busy === "server"}
                  disabled={!baseUrl.trim() || baseUrl.trim() === config.baseUrl}
                />
              </View>
            )}
          </Group>

          {note && <Text style={[ui.meta, styles.note]}>{note}</Text>}
        </ScrollView>
      </View>
    </Modal>
  );
}

function Group({ title, footer, children }: { title: string; footer?: string; children: ReactNode }) {
  return (
    <View style={styles.group}>
      <Text style={ui.label}>{title}</Text>
      <View style={styles.card}>{children}</View>
      {footer && <Text style={[ui.meta, styles.footer]}>{footer}</Text>}
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
  chevron?: boolean | "up" | "down";
  children?: ReactNode;
}) {
  const tint = danger ? colors.danger : colors.text;
  const chevronName =
    chevron === "up" ? "chevron-up" : chevron === "down" ? "chevron-down" : "chevron-forward";
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress || busy}
      style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}
    >
      <Ionicons name={icon} size={19} color={danger ? colors.danger : colors.muted} />
      <Text style={[styles.rowLabel, { color: tint }]} numberOfLines={1}>
        {label}
      </Text>
      <View style={styles.rowRight}>
        {busy ? <ActivityIndicator color={colors.muted} /> : children}
        {chevron && !busy && <Ionicons name={chevronName} size={16} color={colors.faint} />}
      </View>
    </Pressable>
  );
}

function hostOf(url: string): string {
  return url.replace(/^https?:\/\//, "");
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
  top: { flexDirection: "row", alignItems: "center", marginTop: 12, marginBottom: 8 },
  group: { marginTop: 24 },
  card: { backgroundColor: colors.surface, borderRadius: 14, overflow: "hidden" },
  footer: { marginTop: 6, marginHorizontal: 4 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 48,
    paddingHorizontal: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  rowLabel: { fontSize: 16, flexShrink: 1 },
  rowRight: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 8 },
  value: { color: colors.muted, fontSize: 14, flexShrink: 1 },
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
});
