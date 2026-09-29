import { Ionicons } from "@expo/vector-icons";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { confirmDelete } from "../components";
import { formatStamp } from "../format";
import { forget, type OutboxItem, useOutbox } from "../outbox";
import { colors, ui } from "../ui";

// What's on this phone but not yet on the server (ADR 0028). Waiting items sync by
// themselves; one the server refused shows why and can be removed - never dropped.
export function OutboxSection() {
  const items = useOutbox();
  if (items.length === 0) return null;
  return (
    <View style={styles.wrap}>
      <Text style={ui.label}>On this phone</Text>
      {items.map((item) => (
        <Row key={item.clientToken} item={item} />
      ))}
    </View>
  );
}

function Row({ item }: { item: OutboxItem }) {
  const title =
    item.kind === "text" ? (item.text ?? "").split("\n")[0] : item.kind === "audio" ? "Voice note" : "Photo";
  const remove = () => confirmDelete("Remove this note from the phone? It was never saved.", () => void forget(item));
  return (
    <View style={styles.row}>
      <Ionicons
        name={item.failed ? "alert-circle-outline" : "cloud-upload-outline"}
        size={19}
        color={item.failed ? colors.danger : colors.record}
      />
      <View style={{ flex: 1 }}>
        <Text style={styles.title} numberOfLines={1}>
          {title}
        </Text>
        <Text style={[ui.meta, item.failed && { color: colors.danger }]} numberOfLines={2}>
          {item.failed ? `Couldn't save: ${item.failed}` : `Waiting to sync · ${formatStamp(item.capturedAt)}`}
        </Text>
      </View>
      {item.failed && (
        <Pressable onPress={remove} hitSlop={10}>
          <Ionicons name="close" size={18} color={colors.muted} />
        </Pressable>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { paddingHorizontal: 20, paddingTop: 12 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    padding: 14,
    marginBottom: 8,
    borderRadius: 16,
    backgroundColor: colors.surface,
  },
  title: { color: colors.text, fontSize: 16 },
});
