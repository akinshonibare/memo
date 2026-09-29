import { Ionicons } from "@expo/vector-icons";
import type { ComponentProps } from "react";
import {
  ActionSheetIOS,
  ActivityIndicator,
  Alert,
  type GestureResponderEvent,
  Platform,
  Pressable,
  Text,
  View,
} from "react-native";
import Animated, { useAnimatedStyle } from "react-native-reanimated";
import { haptic } from "../haptics";
import { PressableScale, usePop } from "../motion";
import { makeStyles, useTheme } from "../theme";

type IconName = ComponentProps<typeof Ionicons>["name"];

export function IconCircle({
  name,
  onPress,
  size = 44,
  tone = "dark",
  accessibilityLabel,
}: {
  name: IconName;
  onPress?: () => void;
  size?: number;
  tone?: "dark" | "light";
  accessibilityLabel?: string;
}) {
  const { colors } = useTheme();
  const styles = useStyles();
  const light = tone === "light";
  return (
    <PressableScale
      onPress={onPress}
      hitSlop={8}
      scaleTo={0.9}
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? ICON_LABELS[name as string]}
      style={[styles.circle, { width: size, height: size, borderRadius: size / 2 }, light && styles.light]}
      pressedStyle={styles.pressed}
    >
      <Ionicons name={name} size={size * 0.5} color={light ? colors.bg : colors.text} />
    </PressableScale>
  );
}

// VoiceOver reads an icon-only button by its label; these are the icons memo uses.
const ICON_LABELS: Record<string, string> = {
  "chevron-back": "Back",
  close: "Close",
  add: "Add",
  "ellipsis-horizontal": "More",
  play: "Play",
  stop: "Stop",
  "people-outline": "People",
  "reader-outline": "Weekly review",
  "calendar-outline": "Planner",
  "bulb-outline": "Ideas",
  "arrow-up": "Send",
  "headset-outline": "Talk hands-free",
  "volume-high": "Stop speaking",
};

export function Pill({
  label,
  onPress,
  icon,
  tone = "dark",
  disabled,
  busy,
  grow,
}: {
  label: string;
  onPress?: () => void;
  icon?: IconName;
  tone?: "dark" | "light" | "danger";
  disabled?: boolean;
  busy?: boolean;
  grow?: boolean;
}) {
  const { colors } = useTheme();
  const styles = useStyles();
  const fg = tone === "light" ? colors.bg : tone === "danger" ? colors.danger : colors.text;
  return (
    <PressableScale
      onPress={onPress}
      disabled={disabled || busy}
      accessibilityRole="button"
      accessibilityState={{ disabled: !!(disabled || busy), busy: !!busy }}
      style={[styles.pill, tone === "light" && styles.light, grow && styles.grow, disabled && styles.pressed]}
      pressedStyle={styles.pressed}
    >
      {busy ? (
        <ActivityIndicator color={fg} />
      ) : (
        <>
          {icon && <Ionicons name={icon} size={18} color={fg} style={styles.pillIcon} />}
          <Text style={[styles.pillText, { color: fg }]}>{label}</Text>
        </>
      )}
    </PressableScale>
  );
}

// Initials on a tinted disc; a person glyph until a name is set.
export function Avatar({ name, size = 40, onPress }: { name: string | null; size?: number; onPress?: () => void }) {
  const { colors } = useTheme();
  const styles = useStyles();
  const initials = (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (
    <PressableScale
      onPress={onPress}
      disabled={!onPress}
      hitSlop={8}
      scaleTo={0.9}
      accessibilityRole="button"
      accessibilityLabel="Profile"
      style={[styles.avatar, { width: size, height: size, borderRadius: size / 2 }]}
      pressedStyle={styles.pressed}
    >
      {initials ? (
        <Text style={[styles.avatarText, { fontSize: size * 0.38 }]}>{initials}</Text>
      ) : (
        <Ionicons name="person" size={size * 0.46} color={colors.muted} />
      )}
    </PressableScale>
  );
}

// A confirm with one destructive action. iOS gets the action sheet. `onCancel` lets a
// swiped-open row close itself again when the answer is no.
export function confirmDelete(title: string, onDelete: () => void, onCancel?: () => void) {
  haptic.warn();
  if (Platform.OS === "ios") {
    ActionSheetIOS.showActionSheetWithOptions(
      { title, options: ["Delete", "Cancel"], destructiveButtonIndex: 0, cancelButtonIndex: 1 },
      (index) => {
        if (index === 0) onDelete();
        else onCancel?.();
      },
    );
    return;
  }
  Alert.alert(title, undefined, [
    { text: "Cancel", style: "cancel", onPress: onCancel },
    { text: "Delete", style: "destructive", onPress: onDelete },
  ]);
}

// The "Ask anything" bar that sits at the bottom of the home and detail screens.
export function AskBar({ onPress }: { onPress: () => void }) {
  const styles = useStyles();
  return (
    <PressableScale
      onPress={onPress}
      scaleTo={0.98}
      accessibilityRole="button"
      style={styles.askBar}
      pressedStyle={styles.pressed}
    >
      <Text style={styles.askText}>Ask anything</Text>
    </PressableScale>
  );
}

// A todo's checkbox. Ticking it pops the circle (ADR 0032); the caller decides what a
// tick means and passes the press on, so fireflies can rise from where it was.
export function TickCircle({
  done,
  onPress,
  label,
  size = 22,
  color,
  doneColor,
  disabled,
}: {
  done: boolean;
  onPress?: (e: GestureResponderEvent) => void;
  label: string;
  size?: number;
  color: string;
  doneColor: string;
  disabled?: boolean;
}) {
  const [scale, pop] = usePop();
  const animated = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }));
  return (
    <Pressable
      hitSlop={10}
      disabled={disabled || !onPress}
      onPress={(e) => {
        pop();
        onPress?.(e);
      }}
      accessibilityRole="checkbox"
      accessibilityState={{ checked: done, disabled: disabled || !onPress }}
      accessibilityLabel={label}
    >
      <Animated.View style={animated}>
        <Ionicons name={done ? "checkmark-circle" : "ellipse-outline"} size={size} color={done ? doneColor : color} />
      </Animated.View>
    </Pressable>
  );
}

export function SheetHandle() {
  const styles = useStyles();
  return (
    <View style={styles.handleWrap}>
      <View style={styles.handle} />
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  circle: {
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
  },
  light: { backgroundColor: colors.text },
  avatar: { backgroundColor: colors.surfaceAlt, alignItems: "center", justifyContent: "center" },
  avatarText: { color: colors.text, fontWeight: "600", letterSpacing: 0.5 },
  pressed: { opacity: 0.55 },
  pill: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.surface,
    paddingVertical: 10,
    paddingHorizontal: 18,
    borderRadius: 999,
    minHeight: 40,
  },
  grow: { flex: 1 },
  pillIcon: { marginRight: 6 },
  pillText: { fontSize: 15, fontWeight: "600" },
  askBar: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingVertical: 16,
    paddingHorizontal: 20,
    justifyContent: "center",
  },
  askText: { color: colors.muted, fontSize: 16 },
  handleWrap: { alignItems: "center", paddingTop: 8 },
  handle: { width: 40, height: 5, borderRadius: 3, backgroundColor: colors.faint },
}));
