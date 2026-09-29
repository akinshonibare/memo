import { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Mention, PeopleList, Person } from "../api";
import { IconCircle } from "../components";
import { formatAgo } from "../format";
import { haptic } from "../haptics";
import { fonts, useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { MentionCard } from "./PeopleParts";

type Order = "recent" | "name";

// Everyone your notes are about (ADR 0029). Nobody is typed in here: memo meets people
// in notes, and this is where you tidy up after it - answer "which one?", open a
// person, see when you were last in touch. No counts of who you've neglected.
export function PeopleScreen({
  api,
  refreshKey,
  onBack,
  onOpenPerson,
  onOpenCapture,
  onChanged,
}: {
  api: Api;
  refreshKey: number;
  onBack: () => void;
  onOpenPerson: (id: string) => void;
  onOpenCapture: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [list, setList] = useState<PeopleList | null>(null);
  const [waiting, setWaiting] = useState<Mention[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [order, setOrder] = useState<Order>("recent");
  const [query, setQuery] = useState("");

  const load = useCallback(async () => {
    try {
      const people = await api.listPeople();
      setList(people);
      setWaiting(people.unresolved > 0 ? await api.unresolvedMentions() : []);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  const q = query.trim().toLowerCase();
  const people = useMemo(() => {
    const all = (list?.people ?? []).filter(
      (p) => !q || p.name.toLowerCase().includes(q) || p.aliases.some((a) => a.toLowerCase().includes(q)),
    );
    if (order === "name") return [...all].sort((a, b) => a.name.localeCompare(b.name));
    // Most recently in touch first; people you've only heard about, at the end.
    return [...all].sort(
      (a, b) =>
        (b.last_contact_on ?? "").localeCompare(a.last_contact_on ?? "") || a.name.localeCompare(b.name),
    );
  }, [list, order, q]);

  const changed = () => {
    load();
    onChanged();
  };

  const header = (
    <View>
      {list && list.unread_notes > 0 && (
        <Text style={[ui.meta, styles.reading]}>
          Reading your older notes for people · {list.unread_notes} to go
        </Text>
      )}
      {list && (list.unreadable_notes ?? 0) > 0 && (
        <Text style={[ui.meta, styles.reading]}>
          {list.unreadable_notes === 1
            ? "One older note couldn't be read for people."
            : `${list.unreadable_notes} older notes couldn't be read for people.`}{" "}
          Their notes are fine; only who they mention is missing.
        </Text>
      )}
      {waiting.length > 0 && !q && (
        <View style={styles.section}>
          <Text style={ui.label}>{"Who's who"}</Text>
          {waiting.map((m) => (
            <MentionCard
              key={m.id}
              api={api}
              mention={m}
              showExcerpt
              onChanged={changed}
              onOpenPerson={onOpenPerson}
              onOpenNote={onOpenCapture}
            />
          ))}
        </View>
      )}
      {(list?.people.length ?? 0) > 0 && (
        <View style={styles.controls}>
          <TextInput
            style={[ui.input, styles.search]}
            value={query}
            onChangeText={setQuery}
            placeholder="Search people"
            placeholderTextColor={colors.muted}
            autoCorrect={false}
            clearButtonMode="while-editing"
          />
          <View style={styles.toggle}>
            {(["recent", "name"] as const).map((o) => (
              <Pressable
                key={o}
                onPress={() => {
                  haptic.pick();
                  setOrder(o);
                }}
                style={[styles.toggleItem, order === o && styles.toggleOn]}
              >
                <Text style={[styles.toggleText, order === o && styles.toggleTextOn]}>
                  {o === "recent" ? "Recent" : "A–Z"}
                </Text>
              </Pressable>
            ))}
          </View>
        </View>
      )}
    </View>
  );

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={onBack} />
        <Text style={styles.title}>People</Text>
      </View>
      {error && <Text style={[ui.danger, ui.pad, { marginTop: 8 }]}>{error}</Text>}
      <FlatList
        data={people}
        keyExtractor={(p) => p.id}
        contentContainerStyle={{ padding: 20, paddingBottom: 40 + insets.bottom }}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={header}
        renderItem={({ item }) => <Row person={item} onPress={() => onOpenPerson(item.id)} />}
        ItemSeparatorComponent={() => <View style={ui.hairline} />}
        ListEmptyComponent={
          list ? (
            <Text style={[ui.empty, { marginTop: 48 }]}>
              {q
                ? "Nobody by that name."
                : "Nobody yet.\nWrite or say a note about someone, and memo will meet them there."}
            </Text>
          ) : null
        }
      />
    </View>
  );
}

function Row({ person, onPress }: { person: Person; onPress: () => void }) {
  const ui = useUi();
  const styles = useStyles();
  const last = person.last_contact_on ? `In touch ${formatAgo(person.last_contact_on)}` : "Not in touch yet";
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.row, pressed && { opacity: 0.6 }]}>
      <View style={{ flex: 1 }}>
        <Text style={ui.rowTitle}>{person.name}</Text>
        <Text style={ui.meta} numberOfLines={1}>
          {[person.note, last].filter(Boolean).join(" · ")}
        </Text>
      </View>
      {/* Amber marks what is live (brand.md): here, someone it may be time to call. */}
      {person.due && <View style={styles.dot} accessibilityLabel="Time to reach out" />}
    </Pressable>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: fonts.serif, fontSize: 30, color: colors.text, marginLeft: 4 },
  reading: { marginBottom: 12 },
  section: { marginBottom: 20 },
  controls: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 8 },
  search: { flex: 1, paddingVertical: 10 },
  toggle: { flexDirection: "row", backgroundColor: colors.surface, borderRadius: 999, padding: 3 },
  toggleItem: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999 },
  toggleOn: { backgroundColor: colors.text },
  toggleText: { color: colors.muted, fontSize: 14, fontWeight: "600" },
  toggleTextOn: { color: colors.bg },
  row: { flexDirection: "row", alignItems: "center", paddingVertical: 14, gap: 12 },
  dot: { width: 8, height: 8, borderRadius: 4, backgroundColor: colors.record },
}));
