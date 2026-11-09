import { Ionicons } from "@expo/vector-icons";
import type { ComponentProps } from "react";
import { Pressable, Text, View } from "react-native";
import { haptic } from "../haptics";
import { type PlaceKey, useFresh } from "../news";
import { makeStyles, useTheme } from "../theme";
import { fonts, useUi } from "../ui";

type IconName = ComponentProps<typeof Ionicons>["name"];

// The fourth tab: the places memo keeps for you over time that didn't earn a tab of
// their own (ADR 0072). Large labelled rows, a mark where something is new. If one of
// these turns out to be opened every day, it belongs on the bar, not here.
export function MoreScreen({
  onLists,
  onHabits,
  onFaith,
}: {
  onLists: () => void;
  onHabits: () => void;
  // Faith (ADR 0042): only while it's turned on in Settings.
  onFaith?: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const fresh = useFresh();

  const rows: { key: string; icon: IconName; label: string; hint: string; place?: PlaceKey; onPress: () => void }[] = [
    { key: "lists", icon: "list-outline", label: "Lists", hint: "What your notes put on them", onPress: onLists },
    { key: "habits", icon: "leaf-outline", label: "Habits", hint: "What you did, counted", place: "habits", onPress: onHabits },
    ...(onFaith
      ? [{ key: "faith", icon: "book-outline" as const, label: "Within", hint: "Prayers, and what you're reading", place: "faith" as const, onPress: onFaith }]
      : []),
  ];

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <Text style={styles.title}>More</Text>
      </View>
      <View style={[styles.group, { marginHorizontal: 20 }]}>
        {rows.map((row, i) => {
          const marked = !!row.place && fresh.has(row.place);
          return (
            <View key={row.key}>
              {i > 0 && <View style={[ui.hairline, styles.rule]} />}
              <Pressable
                onPress={() => {
                  haptic.pick();
                  row.onPress();
                }}
                style={({ pressed }) => [styles.row, pressed && styles.pressed]}
                accessibilityRole="button"
                accessibilityLabel={marked ? `${row.label}, something new` : row.label}
                accessibilityHint={row.hint}
              >
                <View style={styles.icon}>
                  <Ionicons name={row.icon} size={22} color={colors.text} />
                  {marked && <View style={[styles.dot, { backgroundColor: colors.record }]} />}
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={styles.label}>{row.label}</Text>
                  <Text style={ui.meta}>{row.hint}</Text>
                </View>
                <Ionicons name="chevron-forward" size={18} color={colors.faint} />
              </Pressable>
            </View>
          );
        })}
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, marginBottom: 16 },
  title: { fontFamily: fonts.serif, fontSize: 30, color: colors.text, marginLeft: 4 },
  group: { backgroundColor: colors.surface, borderRadius: 20, overflow: "hidden" },
  rule: { marginLeft: 72 },
  row: { flexDirection: "row", alignItems: "center", gap: 14, paddingHorizontal: 16, paddingVertical: 16 },
  pressed: { opacity: 0.55 },
  icon: {
    width: 42,
    height: 42,
    borderRadius: 21,
    backgroundColor: colors.surfaceAlt,
    alignItems: "center",
    justifyContent: "center",
  },
  dot: { position: "absolute", top: 4, right: 4, width: 8, height: 8, borderRadius: 4 },
  label: { color: colors.text, fontSize: 17, fontWeight: "600", marginBottom: 2 },
}));
