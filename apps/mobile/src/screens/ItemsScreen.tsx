import { useCallback, useEffect, useState } from "react";
import { Pressable, SectionList, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Derivation } from "../api";
import { IconCircle, Pill } from "../components";
import { formatDue, formatWhen } from "../format";
import { colors, ui } from "../ui";
import { DerivationSheet } from "./DerivationSheet";

const SECTIONS: { kind: Derivation["kind"]; title: string }[] = [
  { kind: "todo", title: "Todos" },
  { kind: "event", title: "Events" },
  { kind: "idea", title: "Ideas" },
];

// Everything extracted, across notes. The only place an item whose note was deleted
// (ADR 0022) can be seen when it isn't on Today, so it opens straight into the editor;
// an item with a note opens the note.
export function ItemsScreen({
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
  const insets = useSafeAreaInsets();
  const [items, setItems] = useState<Derivation[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<Derivation | null>(null);

  const load = useCallback(async () => {
    try {
      const all = await api.listDerivations();
      setItems(all.filter((d) => d.status !== "rejected"));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  const review = async (d: Derivation, status: "confirmed" | "rejected") => {
    setBusy(d.id);
    try {
      await api.reviewDerivation(d.id, { status });
      await load();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const sections = SECTIONS.map((s) => ({
    title: s.title,
    data: (items ?? []).filter((d) => d.kind === s.kind),
  })).filter((s) => s.data.length > 0);

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" onPress={onBack} />
      </View>
      <Text style={[ui.h1, ui.pad]}>Items</Text>
      {error && <Text style={[ui.danger, ui.pad, { marginBottom: 8 }]}>{error}</Text>}
      <SectionList
        sections={sections}
        keyExtractor={(d) => d.id}
        stickySectionHeadersEnabled={false}
        contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 40 + insets.bottom }}
        renderSectionHeader={({ section }) => <Text style={[ui.label, styles.header]}>{section.title}</Text>}
        renderItem={({ item }) => (
          <Row
            d={item}
            busy={busy === item.id}
            onPress={() => (item.capture_id ? onOpenCapture(item.capture_id) : setEditing(item))}
            onReview={review}
          />
        )}
        ListEmptyComponent={
          items ? <Text style={[ui.empty, { marginTop: 48 }]}>Nothing extracted yet.</Text> : null
        }
      />
      {editing && (
        <DerivationSheet
          api={api}
          captureId={editing.capture_id}
          kind={editing.kind}
          existing={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            load();
            onChanged();
          }}
        />
      )}
    </View>
  );
}

function Row({
  d,
  busy,
  onPress,
  onReview,
}: {
  d: Derivation;
  busy: boolean;
  onPress: () => void;
  onReview: (d: Derivation, status: "confirmed" | "rejected") => void;
}) {
  const p = d.payload;
  const when =
    "due" in p && p.due
      ? `Due ${formatDue(String(p.due))}`
      : "starts_at" in p && p.starts_at
        ? formatWhen(String(p.starts_at))
        : null;
  const status =
    d.status === "pending_review" ? "to review" : d.status === "user_created" ? "added by you" : null;
  const meta = [when, status, d.capture_id ? null : "note deleted"].filter(Boolean).join(" · ");
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}>
      <Text style={ui.rowTitle}>{String(p.title ?? "")}</Text>
      {meta ? <Text style={ui.meta}>{meta}</Text> : null}
      {d.status === "pending_review" && (
        <View style={styles.actions}>
          <Pill label="Confirm" tone="light" grow busy={busy} onPress={() => onReview(d, "confirmed")} />
          <Pill label="Reject" grow disabled={busy} onPress={() => onReview(d, "rejected")} />
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  topBar: { flexDirection: "row", alignItems: "center", paddingTop: 4 },
  header: { marginTop: 20 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 20,
    padding: 16,
    marginBottom: 10,
    gap: 2,
  },
  actions: { flexDirection: "row", gap: 10, marginTop: 14 },
});
