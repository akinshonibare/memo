import { Ionicons } from "@expo/vector-icons";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, type GestureResponderEvent, Pressable, StyleSheet, Text, useWindowDimensions, View } from "react-native";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, {
  Easing,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withSpring,
  withTiming,
} from "react-native-reanimated";
import { scheduleOnRN } from "react-native-worklets";
import { haptic } from "../haptics";
import { springs } from "../motion";
import {
  type Mode,
  makeStyles,
  oklch,
  paletteFor,
  PRESETS,
  resolveScheme,
  swatch,
  THEME_ORDER,
  type ThemeId,
  type ThemePrefs,
  useTheme,
} from "../theme";
import { useUi } from "../ui";

// Pieces of the Settings sheet: the grouped rows it's built from, and the Appearance
// section (ADR 0031) - System / Light / Dark, the themes, and a custom palette picked
// from two hues.

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

// --- the circular reveal ---------------------------------------------------------------

// Changing the theme fills the sheet from your finger with the new background, switches
// underneath, then lifts away (ADR 0032). With Reduce Motion, it just switches.
export function useReveal(): {
  layer: ReactNode;
  reveal: (patch: Partial<ThemePrefs>, e?: GestureResponderEvent) => void;
  hostRef: React.RefObject<View | null>;
} {
  const { prefs, systemScheme, setPrefs } = useTheme();
  const reduced = useReducedMotion();
  const { width, height } = useWindowDimensions();
  const hostRef = useRef<View>(null);
  const [circle, setCircle] = useState<{ x: number; y: number; color: string } | null>(null);
  const grow = useSharedValue(0);
  const fade = useSharedValue(1);
  const pending = useRef<Partial<ThemePrefs> | null>(null);
  const diameter = 2.2 * Math.hypot(width, height);

  const apply = useCallback(() => {
    if (pending.current) setPrefs(pending.current);
    pending.current = null;
    // Let the new theme paint underneath, then lift the circle off it.
    setTimeout(() => {
      fade.set(withTiming(0, { duration: 260 }, (done) => done && scheduleOnRN(setCircle, null)));
    }, 60);
  }, [setPrefs, fade]);

  const reveal = (patch: Partial<ThemePrefs>, e?: GestureResponderEvent) => {
    // A change made while a circle is still growing joins the change it's revealing, so
    // neither is lost when that one lands.
    const merged = { ...pending.current, ...patch };
    if (reduced || !e) {
      if (pending.current) pending.current = merged;
      setPrefs(patch);
      return;
    }
    const next = { ...prefs, ...merged };
    const color = paletteFor(next, resolveScheme(next.mode, systemScheme)).bg;
    const { pageX, pageY } = e.nativeEvent;
    pending.current = merged;
    hostRef.current?.measureInWindow((hx, hy) => {
      setCircle({ x: pageX - hx, y: pageY - hy, color });
      grow.set(0);
      fade.set(1);
      grow.set(
        withTiming(1, { duration: 440, easing: Easing.out(Easing.cubic) }, (done) => done && scheduleOnRN(apply)),
      );
    });
  };

  const style = useAnimatedStyle(() => ({
    opacity: fade.value,
    transform: [{ scale: grow.value }],
  }));
  const layer = circle ? (
    <Animated.View
      pointerEvents="none"
      style={[
        {
          position: "absolute",
          left: circle.x - diameter / 2,
          top: circle.y - diameter / 2,
          width: diameter,
          height: diameter,
          borderRadius: diameter / 2,
          backgroundColor: circle.color,
        },
        style,
      ]}
    />
  ) : null;
  return { layer, reveal, hostRef };
}

// --- appearance ------------------------------------------------------------------------

const MODES: { value: Mode; label: string }[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

export function AppearanceGroup({ reveal }: { reveal: (patch: Partial<ThemePrefs>, e?: GestureResponderEvent) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { prefs, scheme, setPrefs } = useTheme();
  // The custom hues preview live while you drag; the app changes when you let go.
  const [draft, setDraft] = useState<{ hue: number; accent: number } | null>(null);
  const hue = draft?.hue ?? prefs.hue;
  const accent = draft?.accent ?? prefs.accent;
  const preview = paletteFor({ ...prefs, theme: "custom", hue, accent }, scheme);

  const pickTheme = (theme: ThemeId, e: GestureResponderEvent) => {
    if (theme === prefs.theme) return;
    haptic.pick();
    reveal({ theme }, e);
  };

  return (
    <View style={styles.group}>
      <Text style={ui.label}>Appearance</Text>
      <View style={[styles.groupCard, styles.appearance]}>
        <Segmented options={MODES} value={prefs.mode} onChange={(mode, e) => reveal({ mode }, e)} />
        <View style={styles.themes}>
          {THEME_ORDER.map((id) => {
            const sw = id === "custom" ? { bg: preview.surfaceAlt, accent: preview.record } : swatch(id, prefs, scheme);
            const on = prefs.theme === id;
            return (
              <Pressable
                key={id}
                onPress={(e) => pickTheme(id, e)}
                style={styles.theme}
                accessibilityRole="radio"
                accessibilityState={{ selected: on }}
                accessibilityLabel={id === "custom" ? "Custom" : PRESETS[id].label}
              >
                <View style={[styles.themeRing, on && styles.themeRingOn]}>
                  <View style={[styles.themeSwatch, { backgroundColor: sw.bg }]}>
                    <View style={[styles.themeDot, { backgroundColor: sw.accent }]} />
                  </View>
                </View>
                <Text style={[styles.themeLabel, on && styles.themeLabelOn]}>
                  {id === "custom" ? "Custom" : PRESETS[id].label}
                </Text>
              </Pressable>
            );
          })}
        </View>
        {prefs.theme === "custom" && (
          <View style={styles.custom}>
            <HueSlider
              label="Colour"
              value={hue}
              tone={(h) => oklch(0.62, 0.09, h)}
              onChange={(h) => setDraft({ hue: h, accent })}
              onDone={(h) => {
                setDraft(null);
                setPrefs({ hue: h, accent });
              }}
            />
            <HueSlider
              label="Accent"
              value={accent}
              tone={(h) => oklch(0.74, 0.14, h)}
              onChange={(h) => setDraft({ hue, accent: h })}
              onDone={(h) => {
                setDraft(null);
                setPrefs({ hue, accent: h });
              }}
            />
            <View style={[styles.previewBar, { backgroundColor: preview.bg, borderColor: preview.hairline }]}>
              <Text style={[styles.previewText, { color: preview.text }]}>memo</Text>
              <View style={[styles.previewDot, { backgroundColor: preview.record }]} />
              <View style={{ flex: 1 }} />
              <View style={[styles.previewChip, { backgroundColor: preview.surface }]}>
                <Text style={{ color: preview.muted, fontSize: 12 }}>Ask anything</Text>
              </View>
            </View>
          </View>
        )}
      </View>
      <Text style={[ui.meta, styles.groupFooter]}>
        {prefs.theme === "custom"
          ? "memo keeps every colour readable: the hues are yours, the contrast is checked."
          : "System follows your iPhone's light and dark setting."}
      </Text>
    </View>
  );
}

const STOPS = 24;
const THUMB = 28;

// A hue from 0 to 359, dragged along a ribbon of the colours it can be.
function HueSlider({
  label,
  value,
  tone,
  onChange,
  onDone,
}: {
  label: string;
  value: number;
  tone: (hue: number) => string;
  onChange: (hue: number) => void;
  onDone: (hue: number) => void;
}) {
  const styles = useStyles();
  const [width, setWidth] = useState(0);
  const x = useSharedValue(0);
  const track = Math.max(1, width - THUMB);
  useEffect(() => {
    if (width > 0) x.set((value / 359) * track);
  }, [value, width, track, x]);

  const gesture = Gesture.Pan()
    .minDistance(0)
    .onBegin((e) => {
      const px = Math.min(track, Math.max(0, e.x - THUMB / 2));
      x.set(px);
      scheduleOnRN(onChange, Math.round((px / track) * 359));
    })
    .onUpdate((e) => {
      const px = Math.min(track, Math.max(0, e.x - THUMB / 2));
      x.set(px);
      scheduleOnRN(onChange, Math.round((px / track) * 359));
    })
    .onFinalize(() => {
      scheduleOnRN(onDone, Math.round((x.value / track) * 359));
    });
  const thumb = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }] }));

  return (
    <View style={styles.slider}>
      <Text style={styles.sliderLabel}>{label}</Text>
      <GestureDetector gesture={gesture}>
        <View
          style={styles.sliderTrackWrap}
          onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
          accessible
          accessibilityRole="adjustable"
          accessibilityLabel={label}
          accessibilityValue={{ min: 0, max: 359, now: value, text: `${value} degrees` }}
          accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
          onAccessibilityAction={(e) => {
            const next = (value + (e.nativeEvent.actionName === "increment" ? 15 : 345)) % 360;
            onDone(next);
          }}
        >
          <View style={styles.sliderTrack}>
            {Array.from({ length: STOPS }, (_, i) => (
              <View key={i} style={{ flex: 1, backgroundColor: tone((i / (STOPS - 1)) * 359) }} />
            ))}
          </View>
          <Animated.View style={[styles.thumb, { backgroundColor: tone(value) }, thumb]} />
        </View>
      </GestureDetector>
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
  custom: { gap: 14 },
  slider: { gap: 8 },
  sliderLabel: { color: colors.muted, fontSize: 13, fontWeight: "600" },
  sliderTrackWrap: { height: THUMB, justifyContent: "center" },
  sliderTrack: {
    height: 12,
    marginHorizontal: THUMB / 2,
    borderRadius: 6,
    overflow: "hidden",
    flexDirection: "row",
  },
  thumb: {
    position: "absolute",
    left: 0,
    width: THUMB,
    height: THUMB,
    borderRadius: THUMB / 2,
    borderWidth: 3,
    borderColor: colors.bg,
    shadowColor: "#000",
    shadowOpacity: 0.25,
    shadowRadius: 4,
    shadowOffset: { width: 0, height: 1 },
  },
  previewBar: {
    flexDirection: "row",
    alignItems: "flex-end",
    borderRadius: 14,
    borderWidth: 0.5,
    paddingHorizontal: 14,
    paddingVertical: 10,
  },
  previewText: { fontFamily: "InstrumentSerif_400Regular", fontSize: 24, lineHeight: 28 },
  previewDot: { width: 5, height: 5, borderRadius: 3, marginLeft: 1, marginBottom: 7 },
  previewChip: { borderRadius: 999, paddingHorizontal: 12, paddingVertical: 6, alignSelf: "center" },
}));
