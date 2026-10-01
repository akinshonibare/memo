import { useCallback, useEffect, useState } from "react";
import { FlatList, Modal, Pressable, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { type Api, ApiError, type ListSummary } from "../api";
import { IconCircle, Pill, SheetHandle, SheetHeader } from "../components";
import { Dialogs } from "../dialogs";
import { useRefetchOnFocus } from "../focus";
import { haptic } from "../haptics";
import { useKeyboardHeight } from "../keyboard";
import { LockCover } from "../lock";
import { usePullToRefresh } from "../refresh";
import { makeStyles, useTheme } from "../theme";
import { useUi } from "../ui";

// Lists (ADR 0069): the ones your notes fill ("add milk to the shopping list") and the
// ones you make here. Tap a list for its items.
export function ListsScreen({
  api,
  refreshKey,
  onBack,
  onOpenList,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onOpenList: (id: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const [lists, setLists] = useState<ListSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);

  const load = useCallback(async () => {
    try {
      setLists((await api.getLists()).lists);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);
  const pull = usePullToRefresh(load);
  useRefetchOnFocus(load);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title}>Lists</Text>
        <View style={{ flex: 1 }} />
        <IconCircle name="add" size={38} tone="light" onPress={() => setAdding(true)} accessibilityLabel="New list" />
      </View>
      {error && <Text style={[ui.danger, ui.pad, { marginTop: 8 }]}>{error}</Text>}
      <FlatList
        data={lists ?? []}
        keyExtractor={(l) => l.id}
        refreshControl={pull}
        contentContainerStyle={{ padding: 20, paddingBottom: 40 + insets.bottom }}
        renderItem={({ item }) => (
          <Pressable
            onPress={() => onOpenList(item.id)}
            style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}
            accessibilityRole="button"
            accessibilityLabel={`${item.name}, ${counts(item)}`}
          >
            <Text style={ui.rowTitle}>{item.name}</Text>
            <Text style={[ui.meta, { marginTop: 4 }]}>{counts(item)}</Text>
          </Pressable>
        )}
        ListEmptyComponent={
          lists ? (
            <Text style={[ui.empty, { marginTop: 48 }]}>
              {"No lists yet. Say “add milk to the shopping list” in a note, or make one here."}
            </Text>
          ) : null
        }
      />
      {adding && (
        <AddListSheet
          api={api}
          onClose={() => setAdding(false)}
          onAdded={(made) => {
            void load();
            onOpenList(made.id);
          }}
        />
      )}
    </View>
  );
}

function counts(l: ListSummary): string {
  const open = l.open_count === 0 ? "nothing left" : `${l.open_count} to get`;
  return l.done_count ? `${open} · ${l.done_count} done` : open;
}

function AddListSheet({ api, onClose, onAdded }: { api: Api; onClose: () => void; onAdded: (l: ListSummary) => void }) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const keyboard = useKeyboardHeight();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = async () => {
    const n = name.trim();
    if (!n || busy) return;
    setBusy(true);
    setError(null);
    try {
      const made = await api.createList(n);
      haptic.done();
      onClose();
      onAdded(made);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" allowSwipeDismissal={!busy} onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: 24 + keyboard }]}>
        <SheetHandle />
        <SheetHeader
          onClose={onClose}
          action={<Pill label="Make" tone="light" onPress={() => void save()} busy={busy} disabled={!name.trim()} />}
        />
        <Text style={[ui.h2, styles.heading]}>New list</Text>
        <Text style={ui.label}>Called</Text>
        <TextInput
          style={styles.input}
          value={name}
          onChangeText={setName}
          placeholder="Groceries, books, Lagos trip"
          placeholderTextColor={colors.muted}
          autoFocus
          maxLength={80}
          returnKeyType="done"
          onSubmitEditing={() => void save()}
        />
        <Text style={[ui.meta, { marginTop: 12, lineHeight: 19 }]}>
          Your notes fill it too: say the list by name and what goes on it.
        </Text>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: "InstrumentSerif_400Regular", fontSize: 30, color: colors.text, marginLeft: 4 },
  card: { backgroundColor: colors.surface, borderRadius: 16, padding: 14, marginBottom: 10 },
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  heading: { marginTop: 12, marginBottom: 16 },
  input: {
    color: colors.text,
    fontSize: 17,
    paddingVertical: 10,
    borderBottomWidth: 1,
    borderBottomColor: colors.hairline,
    marginBottom: 18,
  },
}));
