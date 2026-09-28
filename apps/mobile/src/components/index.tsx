import { Ionicons } from "@expo/vector-icons";
import type { ComponentProps } from "react";
import { ActionSheetIOS, ActivityIndicator, Alert, Platform, Pressable, StyleSheet, Text, View } from "react-native";
import { colors } from "../ui";

type IconName = ComponentProps<typeof Ionicons>["name"];

export function IconCircle({
  name,
  onPress,
  size = 44,
  tone = "dark",
}: {
  name: IconName;
  onPress?: () => void;
  size?: number;
  tone?: "dark" | "light";
}) {
  const light = tone === "light";
  return (
    <Pressable
      onPress={onPress}
      hitSlop={8}
      style={({ pressed }) => [
        styles.circle,
        { width: size, height: size, borderRadius: size / 2 },
        light && styles.light,
        pressed && styles.pressed,
      ]}
    >
      <Ionicons name={name} size={size * 0.5} color={light ? colors.bg : colors.text} />
    </Pressable>
  );
}

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
  const fg = tone === "light" ? colors.bg : tone === "danger" ? colors.danger : colors.text;
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled || busy}
      style={({ pressed }) => [
        styles.pill,
        tone === "light" && styles.light,
        grow && styles.grow,
        (disabled || pressed) && styles.pressed,
      ]}
    >
      {busy ? (
        <ActivityIndicator color={fg} />
      ) : (
        <>
          {icon && <Ionicons name={icon} size={18} color={fg} style={styles.pillIcon} />}
          <Text style={[styles.pillText, { color: fg }]}>{label}</Text>
        </>
      )}
    </Pressable>
  );
}

// Initials on a tinted disc; a person glyph until a name is set.
export function Avatar({ name, size = 40, onPress }: { name: string | null; size?: number; onPress?: () => void }) {
  const initials = (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("");
  return (
    <Pressable
      onPress={onPress}
      disabled={!onPress}
      hitSlop={8}
      accessibilityLabel="Profile"
      style={({ pressed }) => [
        styles.avatar,
        { width: size, height: size, borderRadius: size / 2 },
        pressed && styles.pressed,
      ]}
    >
      {initials ? (
        <Text style={[styles.avatarText, { fontSize: size * 0.38 }]}>{initials}</Text>
      ) : (
        <Ionicons name="person" size={size * 0.46} color={colors.muted} />
      )}
    </Pressable>
  );
}

// Native "…" menu with one destructive action. iOS gets the action sheet.
export function confirmDelete(title: string, onDelete: () => void) {
  if (Platform.OS === "ios") {
    ActionSheetIOS.showActionSheetWithOptions(
      { title, options: ["Delete", "Cancel"], destructiveButtonIndex: 0, cancelButtonIndex: 1 },
      (index) => {
        if (index === 0) onDelete();
      },
    );
    return;
  }
  Alert.alert(title, undefined, [
    { text: "Cancel", style: "cancel" },
    { text: "Delete", style: "destructive", onPress: onDelete },
  ]);
}

// The "Ask anything" bar that sits at the bottom of the home and detail screens.
export function AskBar({ onPress }: { onPress: () => void }) {
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.askBar, pressed && styles.pressed]}>
      <Text style={styles.askText}>Ask anything</Text>
    </Pressable>
  );
}

export function SheetHandle() {
  return (
    <View style={styles.handleWrap}>
      <View style={styles.handle} />
    </View>
  );
}

const styles = StyleSheet.create({
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
});
