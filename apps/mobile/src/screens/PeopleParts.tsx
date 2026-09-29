import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useState } from "react";
import {
  ActionSheetIOS,
  ActivityIndicator,
  Alert,
  FlatList,
  Modal,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from "react-native";
import type { Api, Mention, Person, PersonRef } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { contactWord, formatAgo } from "../format";
import { haptic } from "../haptics";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { LockCover } from "../lock";

// Pick one person from everyone memo knows: "It's someone else…" on a note, and
// "Merge into…" on a person's page.
export function PersonPicker({
  api,
  title,
  exclude,
  onPick,
  onClose,
}: {
  api: Api;
  title: string;
  exclude?: string;
  onPick: (person: Person) => void;
  onClose: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [people, setPeople] = useState<Person[] | null>(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listPeople()
      .then((list) => setPeople(list.people.filter((p) => p.id !== exclude)))
      .catch((e) => setError(String(e)));
  }, [api, exclude]);

  const q = query.trim().toLowerCase();
  const shown = useMemo(
    () =>
      (people ?? []).filter(
        (p) => !q || p.name.toLowerCase().includes(q) || p.aliases.some((a) => a.toLowerCase().includes(q)),
      ),
    [people, q],
  );

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" allowSwipeDismissal onRequestClose={onClose}>
      <View style={styles.sheet}>
        <SheetHandle />
        <View style={styles.sheetTop}>
          <Text style={[ui.h2, { flex: 1 }]}>{title}</Text>
          <IconCircle name="close" onPress={onClose} />
        </View>
        <TextInput
          style={[ui.input, { marginBottom: 12 }]}
          value={query}
          onChangeText={setQuery}
          placeholder="Search people"
          placeholderTextColor={colors.muted}
          autoCorrect={false}
          clearButtonMode="while-editing"
        />
        {error && <Text style={ui.danger}>{error}</Text>}
        {!people && !error && <ActivityIndicator color={colors.muted} style={{ marginTop: 24 }} />}
        <FlatList
          data={shown}
          keyExtractor={(p) => p.id}
          keyboardShouldPersistTaps="handled"
          renderItem={({ item }) => (
            <Pressable
              onPress={() => {
                haptic.pick();
                onPick(item);
              }}
              style={({ pressed }) => [styles.pickRow, pressed && styles.pressed]}
            >
              <Text style={ui.rowTitle}>{item.name}</Text>
              <Text style={ui.meta}>{describePerson(item)}</Text>
            </Pressable>
          )}
          ListEmptyComponent={
            people ? <Text style={[ui.empty, { marginTop: 32 }]}>Nobody by that name.</Text> : null
          }
        />
        <LockCover />
      </View>
    </Modal>
  );
}

// "from work · in touch 3 weeks ago": what tells one Tunde from another.
export function describePerson(p: Pick<Person, "note" | "last_contact_on" | "aliases"> | PersonRef): string {
  const parts: string[] = [];
  if (p.note) parts.push(p.note);
  if ("aliases" in p && p.aliases.length) parts.push(`also ${p.aliases.join(", ")}`);
  parts.push(p.last_contact_on ? `in touch ${formatAgo(p.last_contact_on)}` : "not in touch yet");
  return parts.join(" · ");
}

// A person in a note. Linked: a chip that opens them, with a way to say it's someone
// else. Waiting: "Which Tunde?", answered with one tap - the only question memo asks,
// and only when two people could be meant (ADR 0029).
export function MentionCard({
  api,
  mention,
  onChanged,
  onOpenPerson,
  showExcerpt,
  onOpenNote,
}: {
  api: Api;
  mention: Mention;
  onChanged: () => void;
  onOpenPerson: (id: string) => void;
  showExcerpt?: boolean;
  onOpenNote?: (captureId: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [busy, setBusy] = useState<string | null>(null);
  const [picking, setPicking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const answer = async (
    key: string,
    body: { person_id: string } | { new_person: true } | { status: "confirmed" | "rejected" },
  ) => {
    haptic.pick();
    setBusy(key);
    try {
      await api.resolveMention(mention.id, body);
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const notAPerson = () =>
    answer("reject", { status: "rejected" });

  const what = mention.contact === "none" ? null : contactWord(mention.contact).toLowerCase();

  if (mention.person) {
    const person = mention.person;
    const options = [`Open ${person.name}`, "It's someone else…", "Someone new", "Not a person", "Cancel"];
    const choose = (i: number) => {
      if (i === 0) onOpenPerson(person.id);
      if (i === 1) setPicking(true);
      if (i === 2) void answer("new", { new_person: true });
      if (i === 3) void notAPerson();
    };
    const more = () => {
      haptic.tap();
      if (Platform.OS === "ios") {
        ActionSheetIOS.showActionSheetWithOptions(
          {
            title: `"${mention.name}" in this note`,
            options,
            cancelButtonIndex: 4,
            destructiveButtonIndex: 3,
          },
          choose,
        );
        return;
      }
      Alert.alert(`"${mention.name}" in this note`, undefined, [
        ...options.slice(0, 4).map((text, i) => ({ text, onPress: () => choose(i) })),
        { text: "Cancel", style: "cancel" as const },
      ]);
    };
    return (
      <>
        <Pressable
          onPress={() => onOpenPerson(person.id)}
          onLongPress={more}
          style={({ pressed }) => [styles.chip, pressed && styles.pressed]}
          accessibilityHint="Long-press if this is someone else"
        >
          <Ionicons name="person-outline" size={15} color={colors.text} />
          <Text style={styles.chipText}>{person.name}</Text>
          {what && <Text style={styles.chipMeta}>{what}</Text>}
          <Pressable onPress={more} hitSlop={10} style={styles.chipMore}>
            {busy ? (
              <ActivityIndicator size="small" color={colors.muted} />
            ) : (
              <Ionicons name="ellipsis-horizontal" size={15} color={colors.muted} />
            )}
          </Pressable>
        </Pressable>
        {error && <Text style={ui.danger}>{error}</Text>}
        {picking && (
          <PersonPicker
            api={api}
            title={`Who is "${mention.name}"?`}
            exclude={person.id}
            onClose={() => setPicking(false)}
            onPick={(p) => {
              setPicking(false);
              void answer(p.id, { person_id: p.id });
            }}
          />
        )}
      </>
    );
  }

  // Waiting for you: which one?
  return (
    <View style={styles.card}>
      <Text style={ui.meta}>
        {showExcerpt && mention.written_at
          ? new Date(mention.written_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })
          : "Who is this?"}
        {what ? ` · ${what}` : ""}
      </Text>
      <Text style={ui.rowTitle}>Which {mention.full_name ?? mention.name}?</Text>
      {mention.hint || mention.context ? (
        <Text style={ui.bodyMuted}>{[mention.hint, mention.context].filter(Boolean).join(" · ")}</Text>
      ) : null}
      {showExcerpt && mention.excerpt ? (
        <Pressable
          onPress={mention.capture_id && onOpenNote ? () => onOpenNote(mention.capture_id!) : undefined}
          disabled={!mention.capture_id || !onOpenNote}
        >
          <Text style={styles.excerpt} numberOfLines={3}>
            “{mention.excerpt}”
          </Text>
        </Pressable>
      ) : null}
      <View style={styles.choices}>
        {mention.candidates.map((c) => (
          <Pressable
            key={c.id}
            disabled={!!busy}
            onPress={() => answer(c.id, { person_id: c.id })}
            style={({ pressed }) => [styles.choice, pressed && styles.pressed]}
          >
            {busy === c.id ? (
              <ActivityIndicator color={colors.bg} />
            ) : (
              <>
                <Text style={styles.choiceName}>{c.name}</Text>
                <Text style={styles.choiceMeta} numberOfLines={1}>
                  {describePerson(c)}
                </Text>
              </>
            )}
          </Pressable>
        ))}
      </View>
      <View style={styles.actions}>
        <Pill label="Someone new" grow busy={busy === "new"} onPress={() => answer("new", { new_person: true })} />
        <Pill label="Someone else" grow disabled={!!busy} onPress={() => setPicking(true)} />
      </View>
      <Pressable onPress={notAPerson} disabled={!!busy} hitSlop={8} style={styles.notPerson}>
        <Text style={ui.meta}>{busy === "reject" ? "…" : "Not a person"}</Text>
      </Pressable>
      {error && <Text style={ui.danger}>{error}</Text>}
      {picking && (
        <PersonPicker
          api={api}
          title={`Who is "${mention.name}"?`}
          onClose={() => setPicking(false)}
          onPick={(p) => {
            setPicking(false);
            void answer(p.id, { person_id: p.id });
          }}
        />
      )}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  sheet: { flex: 1, backgroundColor: colors.bg, paddingHorizontal: 20, paddingBottom: 24 },
  sheetTop: { flexDirection: "row", alignItems: "center", marginTop: 12, marginBottom: 16, gap: 10 },
  pickRow: {
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    gap: 2,
  },
  pressed: { opacity: 0.6 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    alignSelf: "flex-start",
    gap: 6,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingLeft: 12,
    paddingRight: 6,
    height: 36,
    marginBottom: 8,
  },
  chipText: { color: colors.text, fontSize: 15, fontWeight: "600" },
  chipMeta: { color: colors.muted, fontSize: 13 },
  chipMore: { paddingHorizontal: 6, height: 28, justifyContent: "center" },
  card: { backgroundColor: colors.surface, borderRadius: 20, padding: 16, marginBottom: 12, gap: 2 },
  excerpt: { color: colors.muted, fontSize: 14, lineHeight: 20, marginTop: 8, fontStyle: "italic" },
  choices: { gap: 8, marginTop: 14 },
  choice: {
    backgroundColor: colors.text,
    borderRadius: 16,
    paddingVertical: 10,
    paddingHorizontal: 14,
    minHeight: 48,
    justifyContent: "center",
  },
  choiceName: { color: colors.bg, fontSize: 16, fontWeight: "600" },
  choiceMeta: { color: colors.forest, fontSize: 13, marginTop: 1 },
  actions: { flexDirection: "row", gap: 10, marginTop: 10 },
  notPerson: { alignSelf: "center", marginTop: 12, paddingVertical: 4 },
}));
