import { InstrumentSerif_400Regular, useFonts } from "@expo-google-fonts/instrument-serif";
import { StatusBar } from "expo-status-bar";
import { useEffect, useMemo, useState } from "react";
import { useShareIntent } from "expo-share-intent";
import { AppState, Linking, StyleSheet, View } from "react-native";
import { haptic } from "./src/haptics";
import { preparePhoto } from "./src/photo";
import { publishRecord } from "./src/widgets";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { Api, type Me } from "./src/api";
import { type Config, clearSession, loadConfig, loadOnboarded, saveOnboarded } from "./src/config";
import { AskSheet } from "./src/screens/AskScreen";
import { ComposeSheet } from "./src/screens/ComposeSheet";
import { DetailScreen } from "./src/screens/DetailScreen";
import { HomeScreen } from "./src/screens/HomeScreen";
import { IdeasScreen } from "./src/screens/IdeasScreen";
import { PlannerScreen } from "./src/screens/PlannerScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { OnboardingScreen } from "./src/screens/OnboardingScreen";
import { RecordSheet } from "./src/screens/RecordSheet";
import { ProfileSheet } from "./src/screens/ProfileScreen";
import { ReviewScreen } from "./src/screens/ReviewScreen";
import { colors } from "./src/ui";
import { captureOrQueue, flushOutbox, useOutbox } from "./src/outbox";

type Back = "home" | "planner" | "ideas" | "review";

// memo://record, memo://write, memo://ask: what an iOS Shortcut opens, so the Action
// Button or a Siri phrase goes straight to a capture (ADR 0027).
type LinkAction = "record" | "write" | "ask" | "planner";
function linkAction(url: string | null): LinkAction | null {
  const match = url?.match(/^memo:\/\/(record|write|ask|planner)\b/);
  return match ? (match[1] as LinkAction) : null;
}
type View_ = { name: Back } | { name: "detail"; id: string; back: Back };

export default function App() {
  const [fontsLoaded] = useFonts({ InstrumentSerif_400Regular });
  const [config, setConfig] = useState<Config | undefined>(undefined);
  const [view, setView] = useState<View_>({ name: "home" });
  const [ask, setAsk] = useState(false);
  const [settings, setSettings] = useState(false);
  const [record, setRecord] = useState(false);
  const [compose, setCompose] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);
  const [me, setMe] = useState<Me | null>(null);
  const [meReady, setMeReady] = useState(false);
  const [onboarded, setOnboarded] = useState<boolean | undefined>(undefined);

  useEffect(() => {
    loadOnboarded().then(setOnboarded).catch(() => setOnboarded(true));
  }, []);

  // A link can arrive before sign-in or onboarding finish (a cold start from the Action
  // Button): it waits as pending and runs once the app is ready for it.
  const [pending, setPending] = useState<LinkAction | null>(null);
  useEffect(() => {
    const take = (url: string | null) => {
      const action = linkAction(url);
      if (action) setPending(action);
    };
    Linking.getInitialURL().then(take).catch(() => {});
    const sub = Linking.addEventListener("url", (event) => take(event.url));
    return () => sub.remove();
  }, []);

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

  const ready = !!api && onboarded === true;

  // Shared into memo from another app (ADR 0027). A photo becomes a photo capture at
  // once; a link or text opens a new note prefilled, so you can say why you kept it.
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent();
  const [composeText, setComposeText] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (!ready || !api || !hasShareIntent) return;
    const image = shareIntent.files?.find((f) => f.mimeType.startsWith("image/"));
    const text = [shareIntent.meta?.title, shareIntent.text].filter(Boolean).join("\n").trim();
    resetShareIntent();
    const run = setTimeout(() => {
      setView({ name: "home" });
      if (image) {
        preparePhoto(image.path, image.width, image.height)
          .then((uri) => captureOrQueue(api, { kind: "image", file: uri }))
          .then(() => {
            haptic.done();
            bump();
          })
          .catch(() => {});
      } else if (text) {
        setComposeText(text);
        setCompose(true);
      }
    }, 0);
    return () => clearTimeout(run);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, api, hasShareIntent]);

  // The outbox (ADR 0028) empties whenever it can: on sign-in, on returning to the app,
  // and every 20 seconds while something is waiting.
  const waiting = useOutbox().filter((i) => !i.failed).length;
  useEffect(() => {
    if (!api) return;
    const flush = () => void flushOutbox(api).then(bump);
    flush();
    const sub = AppState.addEventListener("change", (s) => s === "active" && flush());
    const timer = waiting > 0 ? setInterval(flush, 20_000) : null;
    return () => {
      sub.remove();
      if (timer) clearInterval(timer);
    };
  }, [api, waiting]);

  // The Record widget needs one snapshot from the app before it can render.
  useEffect(() => {
    publishRecord();
  }, []);
  useEffect(() => {
    if (!ready || !pending) return;
    const run = setTimeout(() => {
      setView({ name: pending === "planner" ? "planner" : "home" });
      setSettings(false);
      if (pending === "record") setRecord(true);
      if (pending === "write") setCompose(true);
      if (pending === "ask") setAsk(true);
      setPending(null);
    }, 0);
    return () => clearTimeout(run);
  }, [ready, pending]);

  // Who is signed in, for the avatar on Home; refreshed from Profile on every change.
  useEffect(() => {
    if (!api) return;
    api
      .getMe()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setMeReady(true));
  }, [api]);

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
        {api && meReady && onboarded === false && (
          <OnboardingScreen
            api={api}
            me={me}
            onDone={(updated) => {
              if (updated) setMe(updated);
              setOnboarded(true);
              saveOnboarded().catch(() => {});
            }}
          />
        )}
        {api && onboarded !== false && view.name === "home" && (
          <HomeScreen
            api={api}
            refreshKey={refreshKey}
            onOpen={(id) => setView({ name: "detail", id, back: "home" })}
            onPlanner={() => setView({ name: "planner" })}
            onReview={() => setView({ name: "review" })}
            onAsk={() => setAsk(true)}
            onRecord={() => setRecord(true)}
            onCompose={() => setCompose(true)}
            onSettings={() => setSettings(true)}
            name={me?.name ?? null}
          />
        )}
        {api && view.name === "planner" && (
          <PlannerScreen
            api={api}
            refreshKey={refreshKey}
            onBack={() => setView({ name: "home" })}
            onIdeas={() => setView({ name: "ideas" })}
            onOpenCapture={(id) => setView({ name: "detail", id, back: "planner" })}
            onChanged={bump}
          />
        )}
        {api && view.name === "review" && (
          <ReviewScreen
            api={api}
            onBack={() => setView({ name: "home" })}
            onOpenCapture={(id) => setView({ name: "detail", id, back: "review" })}
            onChanged={bump}
          />
        )}
        {api && view.name === "ideas" && (
          <IdeasScreen
            api={api}
            refreshKey={refreshKey}
            onBack={() => setView({ name: "planner" })}
            onOpenCapture={(id) => setView({ name: "detail", id, back: "ideas" })}
            onChanged={bump}
          />
        )}
        {api && view.name === "detail" && (
          <DetailScreen
            key={view.id}
            api={api}
            id={view.id}
            onBack={() => setView({ name: view.back })}
            onAsk={() => setAsk(true)}
            onChanged={bump}
            onOpen={(id) => setView({ name: "detail", id, back: view.back })}
          />
        )}
        {api && (
          <AskSheet
            api={api}
            visible={ask}
            onClose={() => setAsk(false)}
            onOpenCapture={(id) => {
              setAsk(false);
              setView({ name: "detail", id, back: "home" });
            }}
          />
        )}
        {api && record && <RecordSheet api={api} onClose={() => setRecord(false)} onSaved={bump} />}
        {api && compose && (
          <ComposeSheet
            api={api}
            initialText={composeText}
            onClose={() => {
              setCompose(false);
              setComposeText(undefined);
            }}
            onSaved={bump}
          />
        )}
        <ProfileSheet
          visible={settings}
          config={config}
          onClose={() => setSettings(false)}
          onBaseUrlChanged={(baseUrl) => {
            setConfig({ ...config, baseUrl });
            setSettings(false);
          }}
          onSignOut={signOut}
          onProfileChanged={setMe}
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
