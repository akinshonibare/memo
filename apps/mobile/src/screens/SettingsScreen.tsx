import { useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { type Config, saveConfig } from "../config";
import { colors, ui } from "../ui";

export function SettingsScreen({
  initial,
  onSaved,
}: {
  initial: Config | null;
  onSaved: (config: Config) => void;
}) {
  const [baseUrl, setBaseUrl] = useState(initial?.baseUrl ?? "https://");
  const [token, setToken] = useState(initial?.token ?? "");
  const [status, setStatus] = useState<string | null>(null);

  const save = async () => {
    const config = { baseUrl: baseUrl.trim(), token: token.trim() };
    try {
      const ok = await fetch(`${config.baseUrl.replace(/\/+$/, "")}/health`).then((r) => r.ok);
      if (!ok) throw new Error("health check failed");
      await saveConfig(config);
      setStatus("Saved.");
      onSaved(config);
    } catch (error) {
      setStatus(`Could not reach the API: ${String(error)}`);
    }
  };

  return (
    <View style={ui.screen}>
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
      <Pressable style={ui.button} onPress={save}>
        <Text style={ui.buttonText}>Save</Text>
      </Pressable>
      {status && <Text style={ui.cardMeta}>{status}</Text>}
    </View>
  );
}

const styles = StyleSheet.create({
  input: {
    fontSize: 16,
    color: colors.text,
    padding: 12,
    borderRadius: 12,
    backgroundColor: colors.surface,
    marginBottom: 12,
  },
});
