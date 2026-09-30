import { InstrumentSerif_400Regular, useFonts } from "@expo-google-fonts/instrument-serif";
import {
  createNavigationContainerRef,
  DarkTheme,
  DefaultTheme,
  NavigationContainer,
  StackActions,
} from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StatusBar } from "expo-status-bar";
import { useCallback, useEffect, useMemo, useState } from "react";
import { useShareIntent } from "expo-share-intent";
import { AppState, Linking, View } from "react-native";
import { GestureHandlerRootView } from "react-native-gesture-handler";
import { haptic } from "./src/haptics";
import { preparePhoto } from "./src/photo";
import { publishRecord } from "./src/widgets";
import { SafeAreaProvider, SafeAreaView } from "react-native-safe-area-context";
import { Api, type Me, type Space } from "./src/api";
import { type Config, clearSession, loadConfig, loadOnboarded, saveOnboarded } from "./src/config";
import { LockCover, LockProvider, useLock } from "./src/lock";
import { FirefliesHost } from "./src/motion";
import { refreshPushToken, registerMorningCategory, useMorningPushTaps } from "./src/push";
import { useReminderTaps } from "./src/reminders";
import { forgetHealthOnThisPhone, useHealthSync } from "./src/health";
import { Dialogs } from "./src/dialogs";
import { markSeen, ROUTE_PLACE } from "./src/news";
import { clearPhone, usePhoneSync } from "./src/sync";
import { AskSheet } from "./src/screens/AskScreen";
import { ComposeSheet } from "./src/screens/ComposeSheet";
import { DetailScreen } from "./src/screens/DetailScreen";
import { FaithScreen } from "./src/screens/FaithScreen";
import { HabitScreen } from "./src/screens/HabitScreen";
import { HabitsScreen } from "./src/screens/HabitsScreen";
import { HomeScreen } from "./src/screens/HomeScreen";
import { IdeasScreen } from "./src/screens/IdeasScreen";
import { PlannerScreen } from "./src/screens/PlannerScreen";
import { LoginScreen } from "./src/screens/LoginScreen";
import { OnboardingScreen } from "./src/screens/OnboardingScreen";
import { PeopleScreen } from "./src/screens/PeopleScreen";
import { PersonScreen } from "./src/screens/PersonScreen";
import { RecordSheet } from "./src/screens/RecordSheet";
import { ProfileSheet } from "./src/screens/ProfileScreen";
import { ReviewScreen } from "./src/screens/ReviewScreen";
import { ThemeProvider, useTheme } from "./src/theme";
import { captureOrQueue, flushOutbox, onNoteSaved, useOutbox } from "./src/outbox";
import { birthdayToday, milestoneReached, type Moment } from "./src/delight";

// memo://record, write, ask, planner: what a Shortcut or widget opens (ADR 0027).
// memo://capture?text=...: a note dictated to Siri (ADR 0028), saved as it is.
type LinkAction = "record" | "write" | "ask" | "planner" | `capture:${string}`;
function linkAction(url: string | null): LinkAction | null {
  const match = url?.match(/^memo:\/\/(record|write|ask|planner|capture)\b(?:\?(.*))?/);
  if (!match) return null;
  if (match[1] !== "capture") return match[1] as LinkAction;
  const param = /(?:^|&)text=([^&]*)/.exec(match[2] ?? "");
  let text = "";
  try {
    text = param ? decodeURIComponent(param[1].replace(/\+/g, " ")).trim() : "";
  } catch {
    return null; // malformed escape: ignore rather than save garbage
  }
  return text ? `capture:${text}` : null;
}

// A native stack (ADR 0033): real iOS pushes and the edge swipe back. A note or a
// person is pushed on top of wherever it was opened from, so back retraces the path:
// a note -> the person in it -> another of their notes -> back, back.
export type Routes = {
  Home: undefined;
  Planner: undefined;
  Ideas: undefined;
  Review: undefined;
  People: undefined;
  Person: { id: string };
  Detail: { id: string };
  Faith: undefined;
  Habits: undefined;
  Habit: { id: string };
};
const Stack = createNativeStackNavigator<Routes>();
const nav = createNavigationContainerRef<Routes>();

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1 }}>
      <SafeAreaProvider>
        <ThemeProvider>
          <LockProvider>
            <Root />
          </LockProvider>
        </ThemeProvider>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

function Root() {
  const { colors, scheme, ready: themeReady } = useTheme();
  const [fontsLoaded] = useFonts({ InstrumentSerif_400Regular });
  const [config, setConfig] = useState<Config | undefined>(undefined);
  const [ask, setAsk] = useState(false);
  const [settings, setSettings] = useState(false);
  const [record, setRecord] = useState(false);
  const [compose, setCompose] = useState(false);
  // Where the next note written or recorded goes: Faith's buttons say faith (ADR 0042).
  const [space, setSpace] = useState<Space>("main");
  const [refreshKey, setRefreshKey] = useState(0);
  const [me, setMe] = useState<Me | null>(null);
  const [meReady, setMeReady] = useState(false);
  const [onboarded, setOnboarded] = useState<boolean | undefined>(undefined);
  const [navReady, setNavReady] = useState(false);
  const lock = useLock();

  useEffect(() => {
    loadOnboarded().then(setOnboarded).catch(() => setOnboarded(true));
  }, []);

  // A link can arrive before sign-in, onboarding or unlocking finish (a cold start from
  // the Action Button): it waits as pending and runs once the app is ready for it.
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
    // memo's calendar, Reminders list and notifications belong to the account, and so
    // does reading Apple Health: the next person to sign in turns it on for themselves.
    void clearPhone();
    void forgetHealthOnThisPhone();
    // The navigator unmounts with the session; the next sign-in starts again from Home.
    setNavReady(false);
    setConfig((c) => (c ? { ...c, token: null } : c));
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
  const openNote = useCallback((id: string) => {
    if (nav.isReady()) nav.dispatch(StackActions.push("Detail", { id }));
  }, []);
  // A note just saved opens at once, so you watch what memo takes from it arrive (its
  // page polls until extraction is done). One kept offline has no page yet: Home.
  const savedNote = (captureId: string | null) => {
    bump();
    if (captureId) openNote(captureId);
  };
  const openPerson = (id: string) => {
    if (nav.isReady()) nav.dispatch(StackActions.push("Person", { id }));
  };
  const goHome = (then?: "Planner") => {
    if (!nav.isReady()) return;
    nav.reset({ index: then ? 1 : 0, routes: then ? [{ name: "Home" }, { name: then }] : [{ name: "Home" }] });
  };

  // Not while covered either: coming back from the background, a link or a notification
  // tap can arrive before the "active" event that decides whether memo locks.
  const ready = !!api && onboarded === true && navReady && lock.ready && !lock.locked && !lock.covered;

  // Shared into memo from another app (ADR 0027). A photo, a link or text opens a new
  // note with it in (ADR 0056 for photos), so you can say why you kept it.
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent();
  const [composeText, setComposeText] = useState<string | undefined>(undefined);
  const [composePhoto, setComposePhoto] = useState<string | undefined>(undefined);
  useEffect(() => {
    if (!ready || !api || !hasShareIntent) return;
    const image = shareIntent.files?.find((f) => f.mimeType.startsWith("image/"));
    const text = [shareIntent.meta?.title, shareIntent.text].filter(Boolean).join("\n").trim();
    resetShareIntent();
    const run = setTimeout(() => {
      goHome();
      if (image) {
        preparePhoto(image.path, image.width, image.height)
          .then((uri) => {
            setComposePhoto(uri);
            setComposeText(text || undefined);
            setSpace("main");
            setCompose(true);
          })
          .catch(() => {});
      } else if (text) {
        setComposeText(text);
        setSpace("main"); // the share sheet always writes an ordinary note
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

  // memo on the phone - event notifications (ADR 0036), the calendar and Reminders (ADR
  // 0034) - follows whatever memo knows; a todo ticked in Reminders comes back as a
  // change. A tapped notification opens the note the event came from.
  usePhoneSync(api, refreshKey, bump);
  // Workouts from Apple Health (ADR 0050), when the switch is on: on launch and on
  // coming back to memo, at most every quarter hour.
  useHealthSync(api, bump);
  useReminderTaps(ready, (captureId) => (captureId ? openNote(captureId) : goHome("Planner")));
  // The morning push (ADR 0047): a tap opens Today, a 👍 or 👎 is recorded.
  const openToday = useCallback(() => goHome(), []);
  useMorningPushTaps(ready, api, openToday);

  // The Record widget needs one snapshot from the app before it can render. The morning
  // push's 👍/👎 buttons are registered on every launch, so they exist on the first push
  // after an install, whatever state the setting is in (ADR 0047).
  useEffect(() => {
    publishRecord();
    void registerMorningCategory();
  }, []);
  useEffect(() => {
    if (!ready || !pending) return;
    const run = setTimeout(() => {
      goHome(pending === "planner" ? "Planner" : undefined);
      setSettings(false);
      if (pending.startsWith("capture:")) {
        // Straight to the outbox path: saved now, or kept until there's a network.
        void captureOrQueue(api!, { kind: "text", text: pending.slice("capture:".length) }).then(() => {
          haptic.done();
          bump();
        });
      }
      // A Shortcut or widget always captures an ordinary note, wherever the app was.
      if (pending === "record" || pending === "write") setSpace("main");
      if (pending === "record") setRecord(true);
      if (pending === "write") setCompose(true);
      if (pending === "ask") setAsk(true);
      setPending(null);
    }, 0);
    return () => clearTimeout(run);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, pending]);

  // Who is signed in, for the avatar on Home; refreshed from Profile on every change.
  // While the morning push is on, this phone registers again: a token can change, and a
  // new sign-in is a new session for the server to send through.
  useEffect(() => {
    if (!api) return;
    api
      .getMe()
      .then((fresh) => {
        setMe(fresh);
        void refreshPushToken(api, !!fresh.morning_push_at);
      })
      .catch(() => setMe(null))
      .finally(() => setMeReady(true));
  }, [api]);

  // Moments worth marking on Home (ADR 0041): a milestone crossed - checked when memo
  // opens and after every note saved, since Siri and the share sheet add notes too - and
  // memo's birthday, once on the day.
  const [moment, setMoment] = useState<Moment | null>(null);
  const noteMilestone = useCallback((count: number | undefined) => {
    if (count === undefined) return; // an older server
    void milestoneReached(count).then((m) => m && setMoment({ kind: "milestone", count: m }));
  }, []);
  const meId = me?.id;
  const createdAt = me?.created_at;
  const loadedCount = me?.note_count;
  useEffect(() => {
    if (!meId || !createdAt) return;
    noteMilestone(loadedCount);
    void birthdayToday(createdAt).then((years) => years && setMoment({ kind: "birthday", years }));
    // Once per signed-in user: the count after that comes from saved notes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [meId]);
  useEffect(() => {
    if (!api) return;
    return onNoteSaved(() => {
      api
        .getMe()
        .then((fresh) => {
          setMe(fresh);
          noteMilestone(fresh.note_count);
        })
        .catch(() => {});
    });
  }, [api, noteMilestone]);

  // Pushes and the swipe back paint in the theme's colours, never the library's.
  const navTheme = useMemo(() => {
    const base = scheme === "dark" ? DarkTheme : DefaultTheme;
    return {
      ...base,
      dark: scheme === "dark",
      colors: {
        ...base.colors,
        primary: colors.record,
        background: colors.bg,
        card: colors.bg,
        text: colors.text,
        border: colors.hairline,
        notification: colors.record,
      },
    };
  }, [colors, scheme]);

  if (!fontsLoaded || config === undefined || !themeReady) return <View style={{ flex: 1, backgroundColor: colors.bg }} />;

  const showApp = !!api && onboarded !== false;
  return (
    <View style={{ flex: 1, backgroundColor: colors.bg }}>
    <SafeAreaView style={{ flex: 1, backgroundColor: colors.bg }} edges={["top"]}>
      <StatusBar style={scheme === "dark" ? "light" : "dark"} />
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
      {showApp && (
        <NavigationContainer
          ref={nav}
          theme={navTheme}
          onReady={() => setNavReady(true)}
          // Looking at a place (however you got there) is what clears its mark.
          onStateChange={() => {
            const place = ROUTE_PLACE[nav.getCurrentRoute()?.name ?? ""];
            if (place) void markSeen(place);
          }}
        >
          <Stack.Navigator
            screenOptions={{
              headerShown: false,
              contentStyle: { backgroundColor: colors.bg },
              gestureEnabled: true,
              // Swipe left to right anywhere to go back, not just from the edge (Akin,
              // 2026-09-30). The Planner keeps the edge swipe: its rows swipe that way
              // to tick a todo off.
              fullScreenGestureEnabled: true,
            }}
          >
            <Stack.Screen name="Home">
              {({ navigation }) => (
                <HomeScreen
                  api={api}
                  refreshKey={refreshKey}
                  onOpen={openNote}
                  onPlanner={() => navigation.navigate("Planner")}
                  onReview={() => navigation.navigate("Review")}
                  onPeople={() => navigation.navigate("People")}
                  onOpenPerson={openPerson}
                  onChanged={bump}
                  onAsk={() => setAsk(true)}
                  onRecord={() => {
                    setSpace("main");
                    setRecord(true);
                  }}
                  onCompose={(photo) => {
                    setSpace("main");
                    setComposePhoto(photo);
                    setCompose(true);
                  }}
                  onSettings={() => setSettings(true)}
                  name={me?.name ?? null}
                  moment={moment}
                  onMomentSeen={() => setMoment(null)}
                  onFaith={me?.faith_enabled ? () => navigation.navigate("Faith") : undefined}
                  onHabits={() => navigation.navigate("Habits")}
                  onOpenHabit={(id) => navigation.navigate("Habit", { id })}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Planner" options={{ fullScreenGestureEnabled: false }}>
              {({ navigation }) => (
                <PlannerScreen
                  api={api}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onIdeas={() => navigation.navigate("Ideas")}
                  onOpenCapture={openNote}
                  onChanged={bump}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Review">
              {({ navigation }) => (
                <ReviewScreen
                  api={api}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenCapture={openNote}
                  onChanged={bump}
                  translation={me?.bible_translation}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Ideas">
              {({ navigation }) => (
                <IdeasScreen
                  api={api}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenCapture={openNote}
                  onChanged={bump}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="People">
              {({ navigation }) => (
                <PeopleScreen
                  api={api}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenPerson={openPerson}
                  onOpenCapture={openNote}
                  onChanged={bump}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Person">
              {({ navigation, route }) => (
                <PersonScreen
                  api={api}
                  id={route.params.id}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenCapture={openNote}
                  // Only a merge opens another person from here: it replaces this one,
                  // which no longer exists.
                  onOpenPerson={(id) => navigation.replace("Person", { id })}
                  onChanged={bump}
                  habits={!!me?.habits_enabled}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Faith">
              {({ navigation }) => (
                <FaithScreen
                  api={api}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenNote={openNote}
                  onWrite={() => {
                    setSpace("faith");
                    setCompose(true);
                  }}
                  onRecord={() => {
                    setSpace("faith");
                    setRecord(true);
                  }}
                  onChanged={bump}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Habits">
              {({ navigation }) => (
                <HabitsScreen
                  api={api}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenHabit={(id) => navigation.navigate("Habit", { id })}
                  onChanged={bump}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Habit">
              {({ navigation, route }) => (
                <HabitScreen
                  api={api}
                  id={route.params.id}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onOpenCapture={openNote}
                  onChanged={bump}
                />
              )}
            </Stack.Screen>
            <Stack.Screen name="Detail">
              {({ navigation, route }) => (
                <DetailScreen
                  api={api}
                  id={route.params.id}
                  refreshKey={refreshKey}
                  onBack={() => navigation.goBack()}
                  onAsk={() => setAsk(true)}
                  onChanged={bump}
                  // A related note replaces this one: back still returns where you came from.
                  onOpen={(id) => navigation.replace("Detail", { id })}
                  onOpenPerson={openPerson}
                  faith={!!me?.faith_enabled}
                  translation={me?.bible_translation}
                />
              )}
            </Stack.Screen>
          </Stack.Navigator>
        </NavigationContainer>
      )}
      {showApp && <FirefliesHost />}
      {api && (
        <AskSheet
          api={api}
          visible={ask}
          onClose={() => setAsk(false)}
          onOpenCapture={(id) => {
            setAsk(false);
            openNote(id);
          }}
        />
      )}
      {api && record && (
        <RecordSheet
          api={api}
          space={space}
          onClose={() => setRecord(false)}
          onSaved={savedNote}
          onType={() => {
            setRecord(false);
            setCompose(true);
          }}
        />
      )}
      {api && compose && (
        <ComposeSheet
          api={api}
          initialText={composeText}
          initialPhoto={composePhoto}
          space={space}
          onClose={() => {
            setCompose(false);
            setComposeText(undefined);
            setComposePhoto(undefined);
          }}
          onSaved={savedNote}
        />
      )}
      <ProfileSheet
        visible={settings}
        config={config}
        lockEnabled={lock.enabled}
        onLockChanged={lock.setEnabled}
        onClose={() => setSettings(false)}
        onBaseUrlChanged={(baseUrl) => {
          setConfig({ ...config, baseUrl });
          setSettings(false);
        }}
        onSignOut={signOut}
        onProfileChanged={setMe}
        onDataDeleted={() => {
          goHome();
          bump();
        }}
        onSettingsChanged={bump}
      />
    </SafeAreaView>
    {api && <Dialogs />}
    {api && <LockCover />}
    </View>
  );
}
