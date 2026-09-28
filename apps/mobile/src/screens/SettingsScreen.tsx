import { useState } from "react";
import { Modal, StyleSheet, Text, TextInput, View } from "react-native";
import { type Config, saveConfig } from "../config";
import { IconCircle, Pill, SheetHandle } from "../components";
import { colors, ui } from "../ui";

export function SettingsSheet({
  visible,
  initial,
  dismissable,
  onClose,
  onSaved,
}: {
  visible: boolean;
  initial: Config | null;
  dismissable: boolean;
  onClose: () => void;
  onSaved: (config: Config) => void;
}) {
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? "");
  const [token, setToken] = useState(initial?.token ?? "");
  const [status, setStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const save = async () => {
    const config = { baseUrl: baseUrl.trim().replace(/\/+$/, ""), token: token.trim() };
    setBusy(true);
    try {
      const ok = await fetch(`${config.baseUrl}/health`).then((r) => r.ok);
      if (!ok) throw new Error("health check failed");
      await saveConfig(config);
      setStatus(null);
      onSaved(config);
    } catch (error) {
      setStatus(`Could not reach the API: ${String(error)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal
      visible={visible}
      animationType="slide"
      presentationStyle="pageSheet"
      onRequestClose={dismissable ? onClose : undefined}
    >
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.top}>
          <View style={{ flex: 1 }} />
          {dismissable && <IconCircle name="close" onPress={onClose} />}
        </View>
        <Text style={[ui.h2, { marginTop: 12, marginBottom: 20 }]}>Settings</Text>
        <Text style={ui.label}>API base URL</Text>
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
        <Text style={ui.label}>Bearer token</Text>
        <TextInput
          style={styles.input}
          autoCapitalize="none"
          autoCorrect={false}
          secureTextEntry
          value={token}
          onChangeText={setToken}
          placeholder="API_TOKEN from the server"
          placeholderTextColor={colors.muted}
        />
        {status && <Text style={[ui.danger, { marginBottom: 12 }]}>{status}</Text>}
        <View style={styles.actions}>
          <Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!baseUrl.trim() || !token.trim()} />
        </View>
      </View>
    </Modal>
  );
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
  actions: { flexDirection: "row", justifyContent: "flex-end" },
});
