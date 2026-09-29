import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useRef, useState } from "react";
import { AppState, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Pill } from "./components";
import { haptic } from "./haptics";
import { Wordmark } from "./motion";
import { makeStyles } from "./theme";

// Face ID lock (ADR 0035). Off by default. When on, memo asks for Face ID (or the
// passcode) on a cold start and after it has been away for more than a minute, and
// covers itself whenever it leaves the foreground, so the app switcher shows nothing.
//
// The cover is drawn inside every sheet as well as over the app (<LockCover />), not
// presented as a modal of its own: iOS won't present a modal over one already showing,
// so a modal lock silently fails to appear over an open sheet. Nothing underneath is
// unmounted, so a recording in progress keeps recording behind it.

const KEY = "memo.lock";
const AWAY_MS = 60_000;

// memo's own Face ID prompts (unlocking, turning the lock on or off) make the app
// inactive while they show. That isn't leaving: it neither covers the app nor starts
// the away clock.
let prompting = false;
async function authenticate(promptMessage: string): Promise<LocalAuthentication.LocalAuthenticationResult> {
  prompting = true;
  try {
    return await LocalAuthentication.authenticateAsync({ promptMessage });
  } finally {
    prompting = false;
  }
}

export async function loadLockEnabled(): Promise<boolean> {
  try {
    return (await SecureStore.getItemAsync(KEY)) === "1";
  } catch {
    return false;
  }
}

// Turning it on proves Face ID works first, so nobody locks themselves out.
export async function enableLock(): Promise<string | null> {
  const hardware = await LocalAuthentication.hasHardwareAsync();
  const enrolled = hardware && (await LocalAuthentication.isEnrolledAsync());
  if (!enrolled) return "Set up Face ID or a passcode in the iPhone's Settings first.";
  const result = await authenticate("Lock memo with Face ID");
  if (!result.success) return null; // cancelled: stays off, nothing to say
  await SecureStore.setItemAsync(KEY, "1");
  return "on";
}

// Turning it off asks too: otherwise anyone holding the unlocked app could remove it.
export async function disableLock(): Promise<boolean> {
  const result = await authenticate("Turn off the lock");
  if (!result.success) return false;
  await SecureStore.setItemAsync(KEY, "0");
  return true;
}

type LockValue = {
  enabled: boolean;
  locked: boolean;
  covered: boolean;
  ready: boolean;
  setEnabled: (on: boolean) => void;
  unlock: () => Promise<void>;
};

const LockContext = createContext<LockValue>({
  enabled: false,
  locked: false,
  covered: false,
  ready: true,
  setEnabled: () => {},
  unlock: async () => {},
});

export function useLock(): LockValue {
  return useContext(LockContext);
}

export function LockProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState({ enabled: false, locked: false, ready: false });
  const [away, setAway] = useState(false);
  const leftAt = useRef<number | null>(null);

  useEffect(() => {
    loadLockEnabled().then((enabled) => setState({ enabled, locked: enabled, ready: true }));
  }, []);

  useEffect(() => {
    const sub = AppState.addEventListener("change", (next) => {
      if (prompting) return;
      if (next === "active") {
        const gone = leftAt.current;
        leftAt.current = null;
        // Lock before lifting the cover, so there's no frame where neither is up.
        if (gone !== null && Date.now() - gone > AWAY_MS) {
          setState((s) => (s.enabled ? { ...s, locked: true } : s));
        }
        setAway(false);
        return;
      }
      setAway(true);
      if (next === "background" && leftAt.current === null) leftAt.current = Date.now();
    });
    return () => sub.remove();
  }, []);

  const unlock = useCallback(async () => {
    if (prompting) return;
    const result = await authenticate("Unlock memo");
    if (result.success) {
      haptic.done();
      setState((s) => ({ ...s, locked: false }));
      return;
    }
    // The phone no longer has a passcode at all (it was removed after the lock was
    // turned on): Face ID can never succeed, and with no passcode there's nothing
    // left to protect with. Turn the lock off rather than lock the owner out for good.
    if (result.error === "passcode_not_set" || result.error === "not_enrolled") {
      const level = await LocalAuthentication.getEnrolledLevelAsync();
      if (level === LocalAuthentication.SecurityLevel.NONE) {
        await SecureStore.setItemAsync(KEY, "0").catch(() => {});
        setState((s) => ({ ...s, enabled: false, locked: false }));
      }
    }
  }, []);

  // Ask as soon as memo is locked and in the foreground; after a cancel, the button.
  const asked = useRef(false);
  const { locked } = state;
  useEffect(() => {
    if (!locked) {
      asked.current = false;
      return;
    }
    const ask = () => {
      if (asked.current || AppState.currentState !== "active") return;
      asked.current = true;
      void unlock();
    };
    const timer = setTimeout(ask, 250);
    const sub = AppState.addEventListener("change", (s) => s === "active" && ask());
    return () => {
      clearTimeout(timer);
      sub.remove();
    };
  }, [locked, unlock]);

  const setEnabled = useCallback((enabled: boolean) => setState((s) => ({ ...s, enabled, locked: false })), []);
  const value: LockValue = {
    ...state,
    covered: state.enabled && away,
    setEnabled,
    unlock,
  };
  return <LockContext.Provider value={value}>{children}</LockContext.Provider>;
}

// What shows while locked, and as the privacy cover in the app switcher. Put it as the
// last child of the app and of every sheet; only a real lock offers the button.
export function LockCover() {
  const { locked, covered, unlock } = useLock();
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  if (!locked && !covered) return null;
  return (
    <View style={[StyleSheet.absoluteFill, styles.wrap, { paddingBottom: Math.max(insets.bottom, 24) + 24 }]}>
      <View style={{ flex: 1 }} />
      <Wordmark live={false} size={56} style={{ alignSelf: "center" }} />
      <Text style={styles.caption}>Locked</Text>
      <View style={{ flex: 1 }} />
      {locked ? <Pill label="Unlock" icon="lock-open-outline" tone="light" onPress={() => void unlock()} /> : null}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  wrap: { backgroundColor: colors.bg, paddingHorizontal: 32, zIndex: 1000, elevation: 1000 },
  caption: { color: colors.muted, fontSize: 15, textAlign: "center", marginTop: 8 },
}));
