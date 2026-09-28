import * as FileSystem from "expo-file-system/legacy";
import * as Sharing from "expo-sharing";
import { useState } from "react";
import { Alert, Modal, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Api } from "../api";
import { type Config, saveBaseUrl } from "../config";
import { IconCircle, Pill, SheetHandle } from "../components";
import { colors, ui } from "../ui";

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
  const [baseUrl, setBaseUrl] = useState(config.baseUrl);
  const [timezone, setTimezone] = useState(deviceTimezone());
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState<"save" | "signout" | "export" | "delete" | null>(null);
  const [dataStatus, setDataStatus] = useState<string | null>(null);
  const signedIn = !!config.token;

  const save = async () => {
    const next = baseUrl.trim().replace(/\/+$/, "");
    setBusy("save");
    try {
      const ok = await fetch(`${next}/health`).then((r) => r.ok);
      if (!ok) throw new Error("health check failed");
      await saveBaseUrl(next);
      if (signedIn && next === config.baseUrl) {
        await new Api(config).setTimezone(timezone.trim());
      }
      setStatus(null);
      onBaseUrlChanged(next);
    } catch (error) {
      setStatus(`Could not reach the API: ${String(error)}`);
    } finally {
      setBusy(null);
    }
  };

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
  const exportData = async () => {
    setBusy("export");
    setDataStatus(null);
    try {
      const data = await new Api(config).exportEverything();
      const day = new Date().toISOString().slice(0, 10);
      const uri = `${FileSystem.cacheDirectory}memo-export-${day}.json`;
      await FileSystem.writeAsStringAsync(uri, JSON.stringify(data, null, 2));
      await Sharing.shareAsync(uri, { UTI: "public.json", mimeType: "application/json" });
    } catch (error) {
      setDataStatus(`Export failed: ${String(error)}`);
    } finally {
      setBusy(null);
    }
  };

  const deleteData = () =>
    Alert.alert(
      "Delete everything?",
      "Every note, recording, extracted item and conversation is removed for good. You stay signed in. Export first if you want a copy.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete everything",
          style: "destructive",
          onPress: async () => {
            setBusy("delete");
            setDataStatus(null);
            try {
              const api = new Api(config);
              const me = await api.getMe();
              const result = await api.deleteEverything(me.id);
              const notes = result.deleted.captures ?? 0;
              setDataStatus(`Deleted ${notes} note${notes === 1 ? "" : "s"}.`);
              onDataDeleted();
            } catch (error) {
              setDataStatus(`Delete failed: ${String(error)}`);
            } finally {
              setBusy(null);
            }
          },
        },
      ],
    );

  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.top}>
          <View style={{ flex: 1 }} />
          <IconCircle name="close" onPress={onClose} />
        </View>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 40 }}>
          <Text style={[ui.h2, { marginTop: 12, marginBottom: 20 }]}>Settings</Text>
          {signedIn && (
            <>
              <Text style={ui.label}>Timezone</Text>
              <TextInput
                style={styles.input}
                autoCapitalize="none"
                autoCorrect={false}
                value={timezone}
                onChangeText={setTimezone}
                placeholder="America/Toronto"
                placeholderTextColor={colors.muted}
              />
            </>
          )}
          <Text style={ui.label}>Server</Text>
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
          {status && <Text style={[ui.danger, { marginBottom: 12 }]}>{status}</Text>}
          <View style={styles.actions}>
            {signedIn && (
              <Pill label="Sign out" tone="danger" onPress={signOut} busy={busy === "signout"} />
            )}
            <View style={{ flex: 1 }} />
            <Pill label="Save" tone="light" onPress={save} busy={busy === "save"} disabled={!baseUrl.trim()} />
          </View>
          {signedIn && (
            <View style={styles.data}>
              <Text style={ui.label}>Your data</Text>
              <View style={styles.actions}>
                <Pill label="Export" icon="share-outline" onPress={exportData} busy={busy === "export"} />
                <View style={{ flex: 1 }} />
                <Pill label="Delete everything" tone="danger" onPress={deleteData} busy={busy === "delete"} />
              </View>
              {dataStatus && <Text style={[ui.meta, { marginTop: 12 }]}>{dataStatus}</Text>}
            </View>
          )}
        </ScrollView>
      </View>
    </Modal>
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
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  input: {
    fontSize: 17,
    color: colors.text,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 16,
    backgroundColor: colors.surfaceAlt,
    marginBottom: 20,
  },
  actions: { flexDirection: "row", alignItems: "center" },
  data: { marginTop: 36 },
});
