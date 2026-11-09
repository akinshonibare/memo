import { Ionicons } from "@expo/vector-icons";
import type { BottomTabBarProps } from "@react-navigation/bottom-tabs";
import type { ComponentProps } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { haptic } from "../haptics";
import { PressableScale } from "../motion";
import { type PlaceKey, useFresh } from "../news";
import { makeStyles, useTheme } from "../theme";

type IconName = ComponentProps<typeof Ionicons>["name"];

// The four tabs, and which of memo's places each one speaks for when something new
// has arrived there (src/news). The record button is not a tab: it sits in the middle
// of the bar, the biggest thing on the screen, because memo is a capture app first
// and the sections are where what you captured ends up (ADR 0072).
const TABS: Record<string, { icon: IconName; label: string; places: PlaceKey[] }> = {
  Home: { icon: "home-outline", label: "Home", places: [] },
  Planner: { icon: "calendar-outline", label: "Planner", places: ["planner", "review"] },
  People: { icon: "people-outline", label: "People", places: ["people"] },
  More: { icon: "ellipsis-horizontal-circle-outline", label: "More", places: ["habits", "faith"] },
};

export const RECORD_SIZE = 64;
// How far the record button rises above the bar's top edge, into the screen above it.
// Home keeps its Ask pill clear of it.
export const RECORD_LIFT = 14;
const BAR_HEIGHT = 56;

export function TabBar({ state, navigation, onRecord }: BottomTabBarProps & { onRecord: () => void }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const fresh = useFresh();
  // Two tabs each side of the record button.
  const middle = Math.ceil(state.routes.length / 2);

  const tab = (route: (typeof state.routes)[number], index: number) => {
    const focused = state.index === index;
    const spec = TABS[route.name] ?? { icon: "ellipse-outline" as const, label: route.name, places: [] };
    const marked = spec.places.some((p) => fresh.has(p));
    const onPress = () => {
      const event = navigation.emit({ type: "tabPress", target: route.key, canPreventDefault: true });
      if (focused || event.defaultPrevented) return;
      haptic.pick();
      navigation.navigate(route.name);
    };
    return (
      <Pressable
        key={route.key}
        onPress={onPress}
        onLongPress={() => navigation.emit({ type: "tabLongPress", target: route.key })}
        style={styles.tab}
        accessibilityRole="tab"
        accessibilityState={{ selected: focused }}
        accessibilityLabel={marked ? `${spec.label}, something new` : spec.label}
      >
        <View>
          <Ionicons name={spec.icon} size={24} color={focused ? colors.text : colors.muted} />
          {marked && <View style={[styles.dot, { backgroundColor: colors.record }]} />}
        </View>
        <Text style={[styles.label, focused && styles.labelOn]}>{spec.label}</Text>
      </Pressable>
    );
  };

  return (
    <View style={[styles.bar, { paddingBottom: insets.bottom, height: BAR_HEIGHT + insets.bottom }]} accessibilityRole="tablist">
      {state.routes.slice(0, middle).map(tab)}
      <View style={styles.gap} />
      {state.routes.slice(middle).map((route, i) => tab(route, middle + i))}
      <PressableScale
        onPress={() => {
          haptic.press();
          onRecord();
        }}
        scaleTo={0.9}
        style={[styles.record, { top: -RECORD_LIFT }]}
        accessibilityRole="button"
        accessibilityLabel="Record a voice note"
      />
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  bar: {
    flexDirection: "row",
    alignItems: "stretch",
    backgroundColor: colors.bg,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
  },
  tab: { flex: 1, alignItems: "center", justifyContent: "center", gap: 3, paddingTop: 6 },
  gap: { width: RECORD_SIZE + 16 },
  label: { fontSize: 11, color: colors.muted },
  labelOn: { color: colors.text },
  dot: { position: "absolute", top: -1, right: -3, width: 7, height: 7, borderRadius: 3.5 },
  record: {
    position: "absolute",
    left: "50%",
    marginLeft: -RECORD_SIZE / 2,
    width: RECORD_SIZE,
    height: RECORD_SIZE,
    borderRadius: RECORD_SIZE / 2,
    backgroundColor: colors.record,
    borderWidth: 6,
    borderColor: colors.recordRing,
  },
}));
