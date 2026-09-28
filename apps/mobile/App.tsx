import { InstrumentSerif_400Regular, useFonts } from "@expo-google-fonts/instrument-serif";
import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import { StyleSheet, View } from "react-native";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { Api } from "./src/api";
import { type Config, clearSession, loadConfig } from "./src/config";
import { AskSheet } from "./src/screens/AskScreen";
import { ComposeSheet } from "./src/screens/ComposeSheet";
import { DetailScreen } from "./src/screens/DetailScreen";
import { HomeScreen } from "./src/screens/HomeScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { RecordSheet } from "./src/screens/RecordSheet";
import { SettingsSheet } from "./src/screens/SettingsScreen";
import { colors } from "./src/ui";

type View_ = { name: "home" } | { name: "detail"; id: string };

export default function App() {
  const [fontsLoaded] = useFonts({ InstrumentSerif_400Regular });
  const [config, setConfig] = useState<Config | undefined>(undefined);
  const [view, setView] = useState<View_>({ name: "home" });
  const [ask, setAsk] = useState(false);
  const [settings, setSettings] = useState(false);
  const [record, setRecord] = useState(false);
  const [compose, setCompose] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    loadConfig().then(setConfig);
  }, []);

  const signOut = () => {
    clearSession().catch(() => {});
    setConfig((c) => (c ? { ...c, token: null } : c));
    setView({ name: "home" });
    setAsk(false);
    setRecord(false);
    setCompose(false);
    setSettings(false);
  };

  const api = useMemo(() => {
    if (!config?.token) return null;
    const instance = new Api(config);
    // A 401 means the session was revoked or expired: back to sign-in, once.
    instance.onUnauthorized = signOut;
    return instance;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [config?.baseUrl, config?.token]);

  const bump = () => setRefreshKey((k) => k + 1);

  if (!fontsLoaded || config === undefined) return <View style={styles.root} />;

  return (
    <SafeAreaProvider>
      <SafeAreaView style={styles.root} edges={["top"]}>
        <StatusBar style="light" />
        {!api && (
          <LoginScreen
            config={config}
            onSignedIn={(token) => setConfig({ ...config, token })}
            onChangeServer={() => setSettings(true)}
          />
        )}
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
        {api && record && <RecordSheet api={api} onClose={() => setRecord(false)} onSaved={bump} />}
        {api && compose && <ComposeSheet api={api} onClose={() => setCompose(false)} onSaved={bump} />}
        <SettingsSheet
          visible={settings}
          config={config}
          onClose={() => setSettings(false)}
          onBaseUrlChanged={(baseUrl) => {
            setConfig({ ...config, baseUrl });
            setSettings(false);
          }}
          onSignOut={signOut}
          onDataDeleted={() => {
            setView({ name: "home" });
            bump();
          }}
        />
      </SafeAreaView>
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.bg },
});
