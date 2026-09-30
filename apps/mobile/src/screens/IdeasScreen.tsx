import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation, Update } from "../api";
import { IconCircle } from "../components";
import { useUi } from "../ui";
import { makeStyles } from "../theme";
import { usePullToRefresh } from "../refresh";
import { DerivationSheet } from "./DerivationSheet";
import { IdeaThread, UpdateCard } from "./UpdateCard";

// Ideas have no date, so they don't belong on the Planner's week. Newest first; the
// morning view resurfaces old ones one at a time. A later note that seems to carry an
// idea on is asked about at the top (ADR 0048); the ones you said yes to sit under
// the idea, as its thread.
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
  const [asking, setAsking] = useState<Update[]>([]);
  const [threads, setThreads] = useState<Update[]>([]);

  const load = useCallback(async () => {
    try {
      const [all, waiting, said] = await Promise.all([
        api.listDerivations(),
        // An older server has no /v1/updates: the screen still works without them.
        api.listUpdates({ status: "pending", kind: "idea" }).catch(() => []),
        api.listUpdates({ status: "confirmed", kind: "idea" }).catch(() => []),
      ]);
      setIdeas(all.filter((d) => d.kind === "idea" && d.status !== "rejected"));
      setAsking(waiting);
      setThreads(said);
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
        <Text style={styles.title}>Highlights</Text>
        <View style={{ flex: 1 }} />
        <IconCircle name="add" size={38} tone="light" onPress={() => setSheet({})} />
      </View>
      {error && <Text style={[ui.danger, ui.pad, { marginTop: 8 }]}>{error}</Text>}
      <FlatList
        data={ideas ?? []}
        keyExtractor={(d) => d.id}
        refreshControl={pull}
        contentContainerStyle={{ padding: 20, paddingBottom: 40 + insets.bottom }}
        ListHeaderComponent={
          asking.length > 0 ? (
            <View style={{ marginBottom: 6 }}>
              {asking.map((u) => (
                <UpdateCard
                  key={u.id}
                  update={u}
                  onOpenNote={onOpenCapture}
                  onDecide={async (yes) => {
                    await api.answerUpdate(u.id, yes ? "yes" : "no");
                    await load();
                  }}
                />
              ))}
            </View>
          ) : null
        }
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
            <IdeaThread updates={threads.filter((u) => u.target.id === item.id)} onOpenNote={onOpenCapture} />
          </Pressable>
        )}
        ListEmptyComponent={
          ideas ? <Text style={[ui.empty, { marginTop: 48 }]}>No highlights yet.</Text> : null
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
