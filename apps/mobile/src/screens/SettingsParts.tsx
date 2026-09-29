import { Ionicons } from "@expo/vector-icons";
import { type ReactNode, useEffect, useState } from "react";
import { ActivityIndicator, type GestureResponderEvent, Pressable, StyleSheet, Text, View } from "react-native";
import Animated, { useAnimatedStyle, useSharedValue, withSpring } from "react-native-reanimated";
import { haptic } from "../haptics";
import { springs } from "../motion";
import { type Mode, makeStyles, PRESETS, swatch, THEME_ORDER, type ThemeId, useTheme } from "../theme";
import { useUi } from "../ui";

// Pieces of the Settings sheet: the grouped rows it's built from, and the Appearance
// section (ADR 0031) - System / Light / Dark, and the themes. (A custom palette picked
// from two hues was taken out, ADR 0052.)

export function Group({ title, footer, children }: { title: string; footer?: string; children: ReactNode }) {
  const ui = useUi();
  const styles = useStyles();
  return (
    <View style={styles.group}>
      <Text style={ui.label}>{title}</Text>
      <View style={styles.groupCard}>{children}</View>
      {footer && <Text style={[ui.meta, styles.groupFooter]}>{footer}</Text>}
    </View>
  );
}

export function Row({
  label,
  icon,
  onPress,
  danger,
  busy,
  chevron,
  children,
}: {
  label: string;
  icon: keyof typeof Ionicons.glyphMap;
  onPress?: () => void;
  danger?: boolean;
  busy?: boolean;
  chevron?: boolean;
  children?: ReactNode;
}) {
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress || busy}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
    >
      <Ionicons name={icon} size={19} color={danger ? colors.danger : colors.muted} />
      <Text style={[styles.rowLabel, danger && { color: colors.danger }]} numberOfLines={1}>
        {label}
      </Text>
      <View style={styles.rowRight}>
        {busy ? <ActivityIndicator color={colors.muted} /> : children}
        {chevron && !busy && <Ionicons name="chevron-forward" size={16} color={colors.faint} />}
      </View>
    </Pressable>
  );
}

// A choice between a few options, with a marker that glides to the one chosen.
export function Segmented<T extends string>({
  options,
  value,
  onChange,
}: {
  options: { value: T; label: string }[];
  value: T;
  onChange: (value: T, e: GestureResponderEvent) => void;
}) {
  const styles = useStyles();
  const [width, setWidth] = useState(0);
  const index = Math.max(0, options.findIndex((o) => o.value === value));
  const slot = width > 0 ? (width - 6) / options.length : 0;
  const x = useSharedValue(0);
  useEffect(() => {
    if (slot > 0) x.set(withSpring(index * slot, springs.release));
  }, [index, slot, x]);
  const marker = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));
  return (
    <View style={styles.segmented} onLayout={(e) => setWidth(e.nativeEvent.layout.width)} accessibilityRole="tablist">
      {slot > 0 && <Animated.View style={[styles.segMarker, { width: slot }, marker]} />}
      {options.map((o) => (
        <Pressable
          key={o.value}
          onPress={(e) => {
            if (o.value === value) return;
            haptic.pick();
            onChange(o.value, e);
          }}
          style={styles.segItem}
          accessibilityRole="tab"
          accessibilityState={{ selected: o.value === value }}
        >
          <Text style={[styles.segText, o.value === value && styles.segTextOn]}>{o.label}</Text>
        </Pressable>
      ))}
    </View>
  );
}

// --- appearance ------------------------------------------------------------------------

const MODES: { value: Mode; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

// A theme or mode applies at once: no sweep across the screen (Akin, 2026-09-29).
export function AppearanceGroup() {
  const ui = useUi();
  const styles = useStyles();
  const { prefs, scheme, setPrefs } = useTheme();

  const pickTheme = (theme: ThemeId) => {
    if (theme === prefs.theme) return;
    haptic.pick();
    setPrefs({ theme });
  };

  return (
    <View style={styles.group}>
      <Text style={ui.label}>Appearance</Text>
      <View style={[styles.groupCard, styles.appearance]}>
        <Segmented options={MODES} value={prefs.mode} onChange={(mode) => setPrefs({ mode })} />
        <View style={styles.themes}>
          {THEME_ORDER.map((id) => {
            const sw = swatch(id, prefs, scheme);
            const on = prefs.theme === id;
            return (
              <Pressable
                key={id}
                onPress={() => pickTheme(id)}
                style={styles.theme}
                accessibilityRole="radio"
                accessibilityState={{ selected: on }}
                accessibilityLabel={PRESETS[id].label}
              >
                <View style={[styles.themeRing, on && styles.themeRingOn]}>
                  <View style={[styles.themeSwatch, { backgroundColor: sw.bg }]}>
                    <View style={[styles.themeDot, { backgroundColor: sw.accent }]} />
                  </View>
                </View>
                <Text style={[styles.themeLabel, on && styles.themeLabelOn]}>{PRESETS[id].label}</Text>
              </Pressable>
            );
          })}
        </View>
      </View>
      <Text style={[ui.meta, styles.groupFooter]}>
        System follows your iPhone&apos;s light and dark setting.
      </Text>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  group: { marginTop: 26 },
  groupCard: { backgroundColor: colors.surface, borderRadius: 14, overflow: "hidden" },
  groupFooter: { marginTop: 6, marginHorizontal: 4 },
  pressed: { opacity: 0.6 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    minHeight: 48,
    paddingHorizontal: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
  },
  rowLabel: { fontSize: 16, color: colors.text, flexShrink: 1 },
  rowRight: { flex: 1, flexDirection: "row", alignItems: "center", justifyContent: "flex-end", gap: 8 },
  appearance: { padding: 14, gap: 16 },
  segmented: { flexDirection: "row", backgroundColor: colors.surfaceAlt, borderRadius: 999, padding: 3 },
  segMarker: { position: "absolute", top: 3, bottom: 3, left: 3, borderRadius: 999, backgroundColor: colors.text },
  segItem: { flex: 1, alignItems: "center", paddingVertical: 8 },
  segText: { color: colors.muted, fontSize: 14, fontWeight: "600" },
  segTextOn: { color: colors.bg },
  themes: { flexDirection: "row", justifyContent: "space-between" },
  theme: { alignItems: "center", gap: 6, width: 52 },
  themeRing: { padding: 3, borderRadius: 999, borderWidth: 2, borderColor: "transparent" },
  themeRingOn: { borderColor: colors.text },
  themeSwatch: { width: 36, height: 36, borderRadius: 18, alignItems: "center", justifyContent: "center" },
  themeDot: { width: 12, height: 12, borderRadius: 6 },
  themeLabel: { color: colors.muted, fontSize: 12 },
  themeLabelOn: { color: colors.text, fontWeight: "600" },
}));
