import { InstrumentSerif_400Regular, useFonts } from "@expo-google-fonts/instrument-serif";
import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import { StyleSheet, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { Api } from "./src/api";
import { type Config, loadConfig } from "./src/config";
import { AskSheet } from "./src/screens/AskScreen";
import { ComposeSheet } from "./src/screens/ComposeSheet";
import { DetailScreen } from "./src/screens/DetailScreen";
import { HomeScreen } from "./src/screens/HomeScreen";
import { RecordSheet } from "./src/screens/RecordSheet";
import { SettingsSheet } from "./src/screens/SettingsScreen";
import { colors } from "./src/ui";

type View_ = { name: "home" } | { name: "detail"; id: string };

export default function App() {
  const [fontsLoaded] = useFonts({ InstrumentSerif_400Regular });
  const [config, setConfig] = useState<Config | null | undefined>(undefined);
  const [view, setView] = useState<View_>({ name: "home" });
  const [ask, setAsk] = useState(false);
  const [settings, setSettings] = useState(false);
  const [record, setRecord] = useState(false);
  const [compose, setCompose] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    loadConfig().then(setConfig);
  }, []);

  const api = useMemo(() => (config ? new Api(config) : null), [config]);
  const bump = () => setRefreshKey((k) => k + 1);

  if (!fontsLoaded || config === undefined) return <View style={styles.root} />;

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.root} edges={["top"]}>
        <StatusBar style="light" />
        {api && view.name === "home" && (
          <HomeScreen
            api={api}
            refreshKey={refreshKey}
            onOpen={(id) => setView({ name: "detail", id })}
            onAsk={() => setAsk(true)}
            onRecord={() => setRecord(true)}
            onCompose={() => setCompose(true)}
            onSettings={() => setSettings(true)}
          />
        )}
        {api && view.name === "detail" && (
          <DetailScreen
            api={api}
            id={view.id}
            onBack={() => setView({ name: "home" })}
            onAsk={() => setAsk(true)}
            onChanged={bump}
          />
        )}
        {api && (
          <AskSheet
            api={api}
            visible={ask}
            onClose={() => setAsk(false)}
            onOpenCapture={(id) => {
              setAsk(false);
              setView({ name: "detail", id });
            }}
          />
        )}
        {api && record && (
          <RecordSheet api={api} onClose={() => setRecord(false)} onSaved={bump} />
        )}
        {api && compose && (
          <ComposeSheet api={api} onClose={() => setCompose(false)} onSaved={bump} />
        )}
        <SettingsSheet
          visible={settings || !api}
          initial={config}
          dismissable={!!api}
          onClose={() => setSettings(false)}
          onSaved={(c) => {
            setConfig(c);
            setSettings(false);
            setView({ name: "home" });
          }}
        />
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
});
