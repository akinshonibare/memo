import { useCallback, useEffect, useState } from "react";
import { Modal, Pressable, ScrollView, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { type Api, ApiError, type ListDetail, type ListItem } from "../api";
import { confirmDelete, IconCircle, Pill, SheetHandle, SheetHeader, TickCircle } from "../components";
import { Dialogs } from "../dialogs";
import { useRefetchOnFocus } from "../focus";
import { haptic } from "../haptics";
import { useKeyboardHeight } from "../keyboard";
import { LockCover } from "../lock";
import { usePullToRefresh } from "../refresh";
import { makeStyles, useTheme } from "../theme";
import { useUi } from "../ui";

// One list (ADR 0069): what's left to get on top, what's done below. Tick to move an
// item down, tick again to bring it back; type at the top to add; hold to remove.
export function ListScreen({
  api,
  id,
  refreshKey,
  onBack,
  onOpenCapture,
  onChanged,
}: {
  api: Api;
  id: string;
  refreshKey: number;
  onBack: () => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [list, setList] = useState<ListDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const load = useCallback(async () => {
    try {
      setList(await api.getList(id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, id]);
  const pull = usePullToRefresh(load);
  useRefetchOnFocus(load);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  const run = async (key: string, action: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(key);
    try {
      await action();
      await load();
      onChanged();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  };

  const add = () => {
    const title = draft.trim();
    if (!title) return;
    setDraft("");
    haptic.tap();
    void run("add", () => api.addListItem(id, { title }));
  };

  const toggle = (item: ListItem) => {
    haptic.tap();
    void run(`tick:${item.id}`, () => api.updateListItem(id, item.id, { done: !item.done_at }));
  };

  const remove = (item: ListItem) => {
    confirmDelete(`Take “${item.title}” off the list?`, () => {
      void run(`remove:${item.id}`, () => api.removeListItem(id, item.id));
    });
  };

  const open = list?.items.filter((i) => !i.done_at) ?? [];
  const done = list?.items.filter((i) => i.done_at) ?? [];

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title} numberOfLines={1}>
          {list?.name ?? ""}
        </Text>
        <View style={{ flex: 1 }} />
        <IconCircle name="ellipsis-horizontal" size={38} onPress={() => setEditing(true)} accessibilityLabel="List options" />
      </View>
      {error && <Text style={[ui.danger, ui.pad, { marginTop: 8 }]}>{error}</Text>}
      <ScrollView
        refreshControl={pull}
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={{ padding: 20, paddingBottom: 40 + insets.bottom }}
      >
        <View style={styles.addRow}>
          <TextInput
            style={styles.addInput}
            value={draft}
            onChangeText={setDraft}
            placeholder="Add something"
            placeholderTextColor={colors.muted}
            maxLength={200}
            returnKeyType="done"
            blurOnSubmit={false}
            onSubmitEditing={add}
            accessibilityLabel="Add an item"
          />
          <Pill label="Add" tone="light" onPress={add} disabled={!draft.trim()} busy={busy === "add"} />
        </View>
        {open.map((item) => (
          <Row key={item.id} item={item} onToggle={() => toggle(item)} onRemove={() => remove(item)} onOpenCapture={onOpenCapture} />
        ))}
        {list && open.length === 0 && (
          <Text style={[ui.empty, { marginVertical: 24 }]}>
            {done.length ? "All done." : "Nothing on it yet. Add something, or say it in a note."}
          </Text>
        )}
        {done.length > 0 && (
          <>
            <Text style={[ui.label, { marginTop: 20 }]}>Done</Text>
            {done.map((item) => (
              <Row key={item.id} item={item} onToggle={() => toggle(item)} onRemove={() => remove(item)} onOpenCapture={onOpenCapture} />
            ))}
          </>
        )}
      </ScrollView>
      {editing && list && (
        <ListSheet
          api={api}
          list={list}
          onClose={() => setEditing(false)}
          onChanged={() => {
            void load();
            onChanged();
          }}
          onGone={() => {
            onChanged();
            onBack();
          }}
        />
      )}
      <Dialogs />
      <LockCover />
    </View>
  );
}

function Row({
  item,
  onToggle,
  onRemove,
  onOpenCapture,
}: {
  item: ListItem;
  onToggle: () => void;
  onRemove: () => void;
  onOpenCapture: (id: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const done = !!item.done_at;
  return (
    <Pressable
      style={styles.row}
      onLongPress={onRemove}
      onPress={item.capture_id ? () => onOpenCapture(item.capture_id!) : undefined}
      accessibilityRole="button"
      accessibilityLabel={item.capture_id ? `${item.title}, from a note` : item.title}
      accessibilityHint="Hold to take it off the list"
    >
      <TickCircle done={done} onPress={onToggle} label={done ? `Bring back ${item.title}` : `Tick off ${item.title}`} color={colors.muted} doneColor={colors.record} size={24} />
      <View style={{ flex: 1 }}>
        <Text style={[ui.rowTitle, done && styles.doneText]}>{item.title}</Text>
        {item.quantity ? <Text style={[ui.meta, done && styles.doneText]}>{item.quantity}</Text> : null}
      </View>
    </Pressable>
  );
}

// Rename, archive, or delete the list.
function ListSheet({
  api,
  list,
  onClose,
  onChanged,
  onGone,
}: {
  api: Api;
  list: ListDetail;
  onClose: () => void;
  onChanged: () => void;
  onGone: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const keyboard = useKeyboardHeight();
  const [name, setName] = useState(list.name);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const act = async (action: () => Promise<unknown>, after: () => void) => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await action();
      haptic.done();
      onClose();
      after();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };
  const rename = () => {
    const n = name.trim();
    if (!n || n === list.name) return onClose();
    void act(() => api.updateList(list.id, { name: n }), onChanged);
  };

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" allowSwipeDismissal={!busy} onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: 24 + keyboard }]}>
        <SheetHandle />
        <SheetHeader
          onClose={onClose}
          action={<Pill label="Save" tone="light" onPress={rename} busy={busy} disabled={!name.trim()} />}
        />
        <Text style={[ui.h2, styles.heading]}>This list</Text>
        <Text style={ui.label}>Called</Text>
        <TextInput
          style={styles.input}
          value={name}
          onChangeText={setName}
          placeholderTextColor={colors.muted}
          maxLength={80}
          returnKeyType="done"
          onSubmitEditing={rename}
        />
        <View style={styles.actions}>
          <Pill
            label={list.archived ? "Bring back" : "Archive"}
            onPress={() => void act(() => api.updateList(list.id, { archived: !list.archived }), onGone)}
            disabled={busy}
          />
          <Pill
            label="Delete list"
            tone="danger"
            onPress={() =>
              confirmDelete(`Delete “${list.name}” and everything on it?`, () => {
                void act(() => api.deleteList(list.id), onGone);
              })
            }
            disabled={busy}
          />
        </View>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: "InstrumentSerif_400Regular", fontSize: 30, color: colors.text, marginLeft: 4, flexShrink: 1 },
  addRow: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 14 },
  addInput: {
    flex: 1,
    color: colors.text,
    fontSize: 17,
    paddingVertical: 10,
    paddingHorizontal: 14,
    backgroundColor: colors.surface,
    borderRadius: 14,
  },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    backgroundColor: colors.surface,
    borderRadius: 16,
    paddingVertical: 12,
    paddingHorizontal: 14,
    marginBottom: 8,
  },
  doneText: { color: colors.muted, textDecorationLine: "line-through" },
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
  actions: { flexDirection: "row", gap: 10, marginTop: 8 },
}));
