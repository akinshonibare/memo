import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import { Pressable, SafeAreaView, StyleSheet, Text, View } from "react-native";
import { Api } from "./src/api";
import { type Config, loadConfig } from "./src/config";
import { AskScreen } from "./src/screens/AskScreen";
import { CaptureScreen } from "./src/screens/CaptureScreen";
import { ReviewScreen } from "./src/screens/ReviewScreen";
import { SettingsScreen } from "./src/screens/SettingsScreen";
import { colors } from "./src/ui";

type Tab = "capture" | "review" | "ask" | "settings";

export default function App() {
  const [config, setConfig] = useState<Config | null | undefined>(undefined);
  const [tab, setTab] = useState<Tab>("capture");

  useEffect(() => {
    loadConfig().then((loaded) => {
      setConfig(loaded);
      if (!loaded) setTab("settings");
    });
  }, []);

  const api = useMemo(() => (config ? new Api(config) : null), [config]);

  if (config === undefined) return <View style={styles.root} />;

  return (
    <SafeAreaView style={styles.root}>
      <StatusBar style="light" />
      <View style={styles.body}>
        {tab === "settings" || !api ? (
          <SettingsScreen initial={config} onSaved={(c) => { setConfig(c); setTab("capture"); }} />
        ) : tab === "capture" ? (
          <CaptureScreen api={api} />
        ) : tab === "review" ? (
          <ReviewScreen api={api} />
        ) : (
          <AskScreen api={api} onOpenCapture={() => setTab("review")} />
        )}
      </View>
      <View style={styles.tabs}>
        {(["capture", "review", "ask", "settings"] as Tab[]).map((t) => (
          <Pressable key={t} style={styles.tab} onPress={() => setTab(t)}>
            <Text style={[styles.tabText, tab === t && styles.tabActive]}>{t}</Text>
          </Pressable>
        ))}
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
  body: { flex: 1 },
  tabs: {
    flexDirection: "row",
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.surfaceAlt,
    backgroundColor: colors.bg,
  },
  tab: { flex: 1, paddingVertical: 12, alignItems: "center" },
  tabText: { color: colors.muted, textTransform: "capitalize" },
  tabActive: { color: colors.text, fontWeight: "600" },
});
