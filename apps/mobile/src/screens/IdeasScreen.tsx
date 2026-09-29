import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation } from "../api";
import { IconCircle } from "../components";
import { useUi } from "../ui";
import { makeStyles } from "../theme";
import { usePullToRefresh } from "../refresh";
import { DerivationSheet } from "./DerivationSheet";

// Ideas have no date, so they don't belong on the Planner's week. Newest first; the
// morning view resurfaces old ones one at a time.
export function IdeasScreen({
  api,
  refreshKey,
  onBack,
  onOpenCapture,
  onChanged,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const [ideas, setIdeas] = useState<Derivation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [sheet, setSheet] = useState<{ existing?: Derivation } | null>(null);

  const load = useCallback(async () => {
    try {
      const all = await api.listDerivations();
      setIdeas(all.filter((d) => d.kind === "idea" && d.status !== "rejected"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);
  const pull = usePullToRefresh(load);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title}>Ideas</Text>
        <View style={{ flex: 1 }} />
        <IconCircle name="add" size={38} tone="light" onPress={() => setSheet({})} />
      </View>
      {error && <Text style={[ui.danger, ui.pad, { marginTop: 8 }]}>{error}</Text>}
      <FlatList
        data={ideas ?? []}
        keyExtractor={(d) => d.id}
        refreshControl={pull}
        contentContainerStyle={{ padding: 20, paddingBottom: 40 + insets.bottom }}
        renderItem={({ item }) => (
          <Pressable
            onPress={() => setSheet({ existing: item })}
            style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}
          >
            <Text style={ui.rowTitle}>{String(item.payload.title ?? "")}</Text>
            {typeof item.payload.summary === "string" && item.payload.summary ? (
              <Text style={ui.bodyMuted} numberOfLines={3}>
                {item.payload.summary}
              </Text>
            ) : null}
            <Text style={[ui.meta, { marginTop: 6 }]}>
              {new Date(item.created_at).toLocaleDateString(undefined, {
                month: "short",
                day: "numeric",
                year: "numeric",
              })}
              {item.status === "pending_review" ? " · to review" : ""}
            </Text>
          </Pressable>
        )}
        ListEmptyComponent={
          ideas ? <Text style={[ui.empty, { marginTop: 48 }]}>No ideas yet.</Text> : null
        }
      />
      {sheet && (
        <DerivationSheet
          api={api}
          captureId={null}
          kind="idea"
          existing={sheet.existing}
          onOpenNote={onOpenCapture}
          onClose={() => setSheet(null)}
          onSaved={() => {
            load();
            onChanged();
          }}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: "InstrumentSerif_400Regular", fontSize: 30, color: colors.text, marginLeft: 4 },
  card: { backgroundColor: colors.surface, borderRadius: 16, padding: 14, marginBottom: 10 },
}));
