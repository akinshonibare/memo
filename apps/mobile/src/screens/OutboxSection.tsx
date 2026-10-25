import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import type { Api } from "../api";
import { confirmDelete } from "../components";
import { formatStamp } from "../format";
import { forget, type OutboxItem, retry, useOutbox } from "../outbox";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";

// What's on this phone but not yet on the server (ADR 0028). Waiting items sync by
// themselves; one the server refused shows why and can be tried again or removed -
// never dropped (ADR 0071).
export function OutboxSection({ api }: { api: Api }) {
  const ui = useUi();
  const styles = useStyles();
  const items = useOutbox();
  if (items.length === 0) return null;
  return (
    <View style={styles.wrap}>
      <Text style={ui.label}>On this phone</Text>
      {items.map((item) => (
        <Row key={item.clientToken} item={item} api={api} />
      ))}
    </View>
  );
}

function Row({ item, api }: { item: OutboxItem; api: Api }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [trying, setTrying] = useState(false);
  const title =
    item.kind === "text" ? (item.text ?? "").split("\n")[0] : item.kind === "audio" ? "Voice note" : "Photo";
  const remove = () => confirmDelete("Remove this note from the phone? It was never saved.", () => void forget(item));
  const again = async () => {
    setTrying(true);
    try {
      await retry(api, item);
    } finally {
      setTrying(false);
    }
  };
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
          {trying
            ? "Trying again…"
            : item.failed
              ? `Couldn't save: ${item.failed}`
              : `Waiting to sync · ${formatStamp(item.capturedAt)}`}
        </Text>
      </View>
      {item.failed && (
        <>
          <Pressable onPress={() => void again()} hitSlop={10} disabled={trying} accessibilityLabel="Try again">
            <Ionicons name="refresh" size={18} color={trying ? colors.muted : colors.text} />
          </Pressable>
          <Pressable onPress={remove} hitSlop={10} disabled={trying} accessibilityLabel="Remove from this phone">
            <Ionicons name="close" size={18} color={colors.muted} />
          </Pressable>
        </>
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
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
}));
