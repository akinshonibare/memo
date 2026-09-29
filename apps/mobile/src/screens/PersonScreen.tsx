import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActionSheetIOS, Alert, Platform, Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { type Api, ApiError, type Mention, type PersonDetail } from "../api";
import { IconCircle, Pill } from "../components";
import { contactWord, formatAgo, formatDay, formatDue, formatRhythm, formatWhen } from "../format";
import { haptic } from "../haptics";
import { fonts, useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { useNavigation } from "@react-navigation/native";
import { useRefetchOnFocus } from "../focus";
import { PersonPicker } from "./PeopleParts";

// Rhythms offered for "suggest getting in touch"; 30 days is everyone's default.
const RHYTHMS: (number | null)[] = [14, 30, 60, 90, 180, null];

// One person (ADR 0029): when you were last in touch, a one-tap "Spoke to them" (iOS
// gives no app your calls or messages, so memo only knows what it is told), the
// reminder rhythm, what is still open with them, and every note that mentions them.
export function PersonScreen({
  api,
  id,
  refreshKey,
  onBack,
  onOpenCapture,
  onOpenPerson,
  onChanged,
}: {
  api: Api;
  id: string;
  refreshKey: number;
  onBack: () => void;
  onOpenCapture: (id: string) => void;
  onOpenPerson: (id: string) => void;
  onChanged: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [person, setPerson] = useState<PersonDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [merging, setMerging] = useState(false);

  // onBack is a new function on every App render; a ref keeps `load` (and so the
  // fetch) tied to the person, not to App re-rendering.
  const back = useRef(onBack);
  useEffect(() => {
    back.current = onBack;
  }, [onBack]);

  const navigation = useNavigation();
  const load = useCallback(async () => {
    try {
      setPerson(await api.getPerson(id));
      setError(null);
    } catch (e) {
      // Gone: merged into someone else, or their last mention was just rejected
      // (the server removes a person nobody's notes name). Back past them, rather
      // than leaving an empty page in the back stack.
      if (e instanceof ApiError && e.status === 404) {
        // Only while on top: going back from a screen hidden under a note would pop
        // the note instead. Hidden, it waits; coming back into view re-reads, and
        // backs out then.
        if (navigation.isFocused()) back.current();
        return;
      }
      setError(String(e));
    }
  }, [api, id, navigation]);

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);
  // Kept mounted under a note opened from here (ADR 0033): coming back re-reads, and a
  // person who no longer exists is backed out of then, while this screen is on top.
  useRefetchOnFocus(load);

  const run = async (key: string, action: () => Promise<unknown>) => {
    setBusy(key);
    try {
      await action();
      await load();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const spoke = () => {
    haptic.done();
    void run("spoke", () => api.logContact(id));
  };

  const prompt = (title: string, initial: string, save: (value: string) => Promise<unknown>) => {
    if (Platform.OS !== "ios") return; // Alert.prompt is iOS-only; memo ships on iOS
    Alert.prompt(
      title,
      undefined,
      (value) => {
        const v = value?.trim();
        if (v !== undefined) void run("edit", () => save(v));
      },
      "plain-text",
      initial,
    );
  };

  const menu = () => {
    if (!person) return;
    haptic.tap();
    const options = ["Rename", "Add another name", person.note ? "Edit note" : "Add a note", "Merge into…", "Cancel"];
    const choose = (i: number) => {
      if (i === 0) prompt("Name", person.name, (name) => api.updatePerson(id, { name }));
      if (i === 1)
        prompt("Another name for them", "", (alias) =>
          alias ? api.updatePerson(id, { aliases: [...person.aliases, alias] }) : Promise.resolve(),
        );
      if (i === 2) prompt("A few words, like “from work”", person.note ?? "", (note) => api.updatePerson(id, { note: note || null }));
      if (i === 3) setMerging(true);
    };
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions({ options, cancelButtonIndex: 4 }, choose);
      return;
    }
    Alert.alert(person.name, undefined, [
      ...options.slice(0, 4).map((text, i) => ({ text, onPress: () => choose(i) })),
      { text: "Cancel", style: "cancel" as const },
    ]);
  };

  const pickRhythm = () => {
    if (!person) return;
    haptic.tap();
    const labels = RHYTHMS.map((d) => (d == null ? "Never" : formatRhythm(d).replace(/^every/, "Every")));
    const choose = (i: number) => {
      if (i >= RHYTHMS.length) return;
      void run("rhythm", () => api.updatePerson(id, { reach_out_every_days: RHYTHMS[i] }));
    };
    if (Platform.OS === "ios") {
      ActionSheetIOS.showActionSheetWithOptions(
        { title: "Suggest getting in touch", options: [...labels, "Cancel"], cancelButtonIndex: labels.length },
        choose,
      );
      return;
    }
    Alert.alert("Suggest getting in touch", undefined, [
      ...labels.map((text, i) => ({ text, onPress: () => choose(i) })),
      { text: "Cancel", style: "cancel" as const },
    ]);
  };

  // A mis-tapped "Spoke to them" (here or on Today) is undone by rejecting the
  // contact it logged: last contact falls back to the one before.
  const removeContact = (m: Mention) => {
    if (!person) return;
    haptic.warn();
    Alert.alert(
      "Remove this contact?",
      `It will no longer count as being in touch with ${person.name}.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove",
          style: "destructive",
          onPress: () => void run("undo", () => api.resolveMention(m.id, { status: "rejected" })),
        },
      ],
    );
  };

  const removeAlias = (alias: string) => {
    if (!person) return;
    haptic.warn();
    Alert.alert(`Stop matching “${alias}”?`, `Notes that say “${alias}” won't link to ${person.name}.`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove",
        style: "destructive",
        onPress: () => void run("alias", () => api.updatePerson(id, { aliases: person.aliases.filter((a) => a !== alias) })),
      },
    ]);
  };

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" onPress={onBack} />
        <View style={{ flex: 1 }} />
        {person && <IconCircle name="ellipsis-horizontal" onPress={menu} />}
      </View>
      <ScrollView contentContainerStyle={[ui.pad, { paddingBottom: 60 + insets.bottom }]}>
        {error && <Text style={[ui.danger, { marginBottom: 12 }]}>{error}</Text>}
        {person && (
          <>
            <Text style={[ui.h1, { marginTop: 0, marginBottom: 4 }]}>{person.name}</Text>
            {person.note ? <Text style={ui.bodyMuted}>{person.note}</Text> : null}
            {person.aliases.length > 0 && (
              <View style={styles.aliases}>
                <Text style={ui.meta}>Also</Text>
                {person.aliases.map((a) => (
                  <Pressable key={a} onPress={() => removeAlias(a)} style={({ pressed }) => [styles.alias, pressed && styles.pressed]}>
                    <Text style={styles.aliasText}>{a}</Text>
                    <Ionicons name="close" size={13} color={colors.muted} />
                  </Pressable>
                ))}
              </View>
            )}

            <View style={styles.card}>
              <Text style={ui.meta}>Last in touch</Text>
              <Text style={styles.big}>
                {person.last_contact_on ? formatAgo(person.last_contact_on) : "Not yet"}
              </Text>
              {person.last_contact_on && <Text style={ui.meta}>{formatDay(person.last_contact_on, true)}</Text>}
              <View style={styles.actions}>
                <Pill label="Spoke to them" icon="checkmark" tone="light" grow busy={busy === "spoke"} onPress={spoke} />
              </View>
              <Pressable onPress={pickRhythm} style={({ pressed }) => [styles.rhythm, pressed && styles.pressed]}>
                <Ionicons name="repeat" size={18} color={colors.muted} />
                <Text style={styles.rhythmText}>
                  {person.reach_out_every_days == null
                    ? "No reminders"
                    : `Suggest getting in touch ${formatRhythm(person.reach_out_every_days)}`}
                </Text>
                <Ionicons name="chevron-forward" size={16} color={colors.faint} />
              </Pressable>
              {person.due && <Text style={styles.due}>A good time to get in touch.</Text>}
            </View>

            {person.open_todos.length > 0 && (
              <View style={styles.section}>
                <Text style={ui.label}>Still to do</Text>
                {person.open_todos.map((t) => (
                  <Item
                    key={t.id}
                    icon="ellipse-outline"
                    title={String(t.payload.title ?? "")}
                    detail={typeof t.payload.due === "string" ? `Due ${formatDue(t.payload.due)}` : null}
                    onPress={t.capture_id ? () => onOpenCapture(t.capture_id!) : undefined}
                  />
                ))}
              </View>
            )}
            {person.upcoming_events.length > 0 && (
              <View style={styles.section}>
                <Text style={ui.label}>Coming up</Text>
                {person.upcoming_events.map((e) => (
                  <Item
                    key={e.id}
                    icon="calendar-outline"
                    title={String(e.payload.title ?? "")}
                    detail={typeof e.payload.starts_at === "string" ? formatWhen(e.payload.starts_at) : null}
                    onPress={e.capture_id ? () => onOpenCapture(e.capture_id!) : undefined}
                  />
                ))}
              </View>
            )}

            {(person.prayers?.length ?? 0) > 0 && (
              <View style={styles.section}>
                <Text style={ui.label}>Praying for them</Text>
                {person.prayers!.map((p) => (
                  <Item
                    key={p.id}
                    icon={p.answered_at ? "checkmark-circle" : "ellipse-outline"}
                    title={p.title}
                    detail={p.answered_at ? (p.answer ? `Answered. ${p.answer}` : "Answered") : p.notes}
                    onPress={p.capture_id ? () => onOpenCapture(p.capture_id!) : undefined}
                  />
                ))}
              </View>
            )}

            <View style={styles.section}>
              <Text style={ui.label}>
                {person.mentions.length === 1 ? "1 note" : `${person.mentions.length} notes`}
              </Text>
              {person.mentions.map((m) => (
                <Timeline key={m.id} mention={m} onOpenCapture={onOpenCapture} onRemove={removeContact} />
              ))}
            </View>
          </>
        )}
      </ScrollView>
      {merging && person && (
        <PersonPicker
          api={api}
          title={`${person.name} is…`}
          exclude={person.id}
          onClose={() => setMerging(false)}
          onPick={(into) => {
            setMerging(false);
            haptic.warn();
            Alert.alert(
              `Merge ${person.name} into ${into.name}?`,
              `Every note about ${person.name} moves to ${into.name}, and “${person.name}” becomes one of their names.`,
              [
                { text: "Cancel", style: "cancel" },
                {
                  text: "Merge",
                  onPress: async () => {
                    try {
                      await api.mergePerson(person.id, into.id);
                      onChanged();
                      onOpenPerson(into.id);
                    } catch (e) {
                      setError(String(e));
                    }
                  },
                },
              ],
            );
          }}
        />
      )}
    </View>
  );
}

function Timeline({
  mention,
  onOpenCapture,
  onRemove,
}: {
  mention: Mention;
  onOpenCapture: (id: string) => void;
  onRemove: (m: Mention) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const when = mention.occurred_on ?? mention.written_at ?? mention.created_at;
  const day = new Date(when.length === 10 ? `${when}T00:00:00` : when).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
  const open = mention.capture_id ? () => onOpenCapture(mention.capture_id!) : undefined;
  // A contact you logged has no note to open; tapping it offers to remove it.
  const logged = mention.status === "user_created" && !mention.capture_id;
  const press = open ?? (logged ? () => onRemove(mention) : undefined);
  return (
    <Pressable
      onPress={press}
      disabled={!press}
      accessibilityHint={logged ? "Remove this contact" : undefined}
      style={({ pressed }) => [styles.entry, pressed && styles.pressed]}
    >
      <Text style={ui.meta}>
        {day} · {contactWord(mention.contact)}
        {logged ? " · logged by you · tap to remove" : mention.status === "user_created" ? " · logged by you" : ""}
        {mention.status === "pending_review" ? " · unconfirmed" : ""}
      </Text>
      {mention.context ? <Text style={styles.entryText}>{mention.context}</Text> : null}
      {mention.excerpt ? (
        <Text style={ui.bodyMuted} numberOfLines={2}>
          {mention.excerpt}
        </Text>
      ) : null}
    </Pressable>
  );
}

function Item({
  icon,
  title,
  detail,
  onPress,
}: {
  icon: "calendar-outline" | "ellipse-outline" | "checkmark-circle";
  title: string;
  detail: string | null;
  onPress?: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  return (
    <Pressable onPress={onPress} disabled={!onPress} style={({ pressed }) => [styles.item, pressed && styles.pressed]}>
      <Ionicons name={icon} size={20} color={colors.muted} style={{ marginRight: 12, marginTop: 2 }} />
      <View style={{ flex: 1 }}>
        <Text style={styles.itemTitle}>{title}</Text>
        {detail ? <Text style={ui.meta}>{detail}</Text> : null}
      </View>
    </Pressable>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, marginBottom: 12 },
  pressed: { opacity: 0.6 },
  aliases: { flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6, marginTop: 8 },
  alias: {
    flexDirection: "row",
    alignItems: "center",
    gap: 4,
    backgroundColor: colors.surface,
    borderRadius: 999,
    paddingHorizontal: 10,
    height: 28,
  },
  aliasText: { color: colors.text, fontSize: 14 },
  card: { backgroundColor: colors.surface, borderRadius: 20, padding: 16, marginTop: 20, gap: 2 },
  big: { fontFamily: fonts.serif, fontSize: 30, lineHeight: 36, color: colors.text },
  actions: { flexDirection: "row", marginTop: 14 },
  rhythm: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    marginTop: 14,
    paddingTop: 12,
    borderTopWidth: StyleSheet.hairlineWidth,
    borderTopColor: colors.hairline,
  },
  rhythmText: { flex: 1, color: colors.text, fontSize: 15 },
  due: { color: colors.record, fontSize: 14, marginTop: 10 },
  section: { marginTop: 28 },
  item: { flexDirection: "row", alignItems: "flex-start", paddingVertical: 8 },
  itemTitle: { color: colors.text, fontSize: 16, fontWeight: "500" },
  entry: {
    paddingVertical: 12,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.hairline,
    gap: 3,
  },
  entryText: { color: colors.text, fontSize: 16, lineHeight: 22 },
}));
