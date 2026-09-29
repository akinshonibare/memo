import * as SecureStore from "expo-secure-store";
import * as SystemUI from "expo-system-ui";
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Appearance, StyleSheet, useColorScheme } from "react-native";
import {
  DEFAULT_PREFS,
  type Palette,
  paletteFor,
  parsePrefs,
  resolveScheme,
  type Scheme,
  type ThemePrefs,
} from "./palette";

export * from "./palette";

// Appearance and theme are a per-device choice, like iOS's own setting (ADR 0031), so
// they live on the phone rather than on the account.
const KEY = "memo.theme";

type ThemeValue = {
  colors: Palette;
  scheme: Scheme;
  // The phone's own setting, remembered even while an explicit Light or Dark hides it:
  // what "System" would resolve to if chosen now.
  systemScheme: Scheme;
  prefs: ThemePrefs;
  ready: boolean;
  setPrefs: (patch: Partial<ThemePrefs>) => void;
};

// Read once at startup, before memo sets any override of its own.
const launchScheme: Scheme = Appearance.getColorScheme() === "light" ? "light" : "dark";

const ThemeContext = createContext<ThemeValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefsState] = useState<ThemePrefs>(DEFAULT_PREFS);
  const [ready, setReady] = useState(false);
  const system = useColorScheme();
  const scheme = resolveScheme(prefs.mode, system);
  // While the choice is System, useColorScheme is the phone's setting; otherwise it
  // reports memo's own override, so the last honest reading is kept.
  const [systemScheme, setSystemScheme] = useState<Scheme>(launchScheme);
  if (ready && prefs.mode === "system" && resolveScheme("system", system) !== systemScheme) {
    setSystemScheme(resolveScheme("system", system));
  }
  const colors = useMemo(() => paletteFor(prefs, scheme), [prefs, scheme]);

  useEffect(() => {
    SecureStore.getItemAsync(KEY)
      .then((raw) => setPrefsState(parsePrefs(raw)))
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  // An explicit Light or Dark is set on the app itself, so what iOS draws for memo -
  // action sheets, alerts, the keyboard, date pickers - matches without each of them
  // being told. System hands the choice back to iOS.
  useEffect(() => {
    if (!ready) return;
    Appearance.setColorScheme(prefs.mode === "system" ? "unspecified" : prefs.mode);
  }, [ready, prefs.mode]);

  // The root view shows behind a sheet as it slides up; it follows the palette so
  // that moment never flashes the other scheme's colour.
  useEffect(() => {
    SystemUI.setBackgroundColorAsync(colors.bg).catch(() => {});
  }, [colors.bg]);

  const setPrefs = useCallback((patch: Partial<ThemePrefs>) => {
    setPrefsState((prev) => {
      const next = { ...prev, ...patch };
      SecureStore.setItemAsync(KEY, JSON.stringify(next)).catch(() => {});
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({ colors, scheme, systemScheme, prefs, ready, setPrefs }),
    [colors, scheme, systemScheme, prefs, ready, setPrefs],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeValue {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme needs a ThemeProvider above it");
  return value;
}

// A stylesheet that follows the theme. Declared once per file, like StyleSheet.create,
// and called as a hook inside each component that uses it. One stylesheet is built per
// palette and reused, so a render costs a lookup, not a StyleSheet.create.
export function makeStyles<T extends StyleSheet.NamedStyles<T>>(factory: (c: Palette) => T): () => T {
  const cache = new WeakMap<Palette, T>();
  return function useStyles(): T {
    const { colors } = useTheme();
    let styles = cache.get(colors);
    if (!styles) {
      styles = StyleSheet.create(factory(colors));
      cache.set(colors, styles);
    }
    return styles;
  };
}
