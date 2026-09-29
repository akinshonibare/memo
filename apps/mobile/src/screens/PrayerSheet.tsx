import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Modal, Pressable, ScrollView, Text, TextInput, useWindowDimensions, View } from "react-native";
import { type Api, ApiError, type Prayer } from "../api";
import { confirmDelete, confirmDiscard, Pill, SheetAction, SheetActions, SheetHandle, SheetHeader } from "../components";
import { ask, Dialogs } from "../dialogs";
import { formatDay } from "../format";
import { haptic } from "../haptics";
import { useKeyboardHeight } from "../keyboard";
import { LockCover } from "../lock";
import { celebrate } from "../motion";
import { makeStyles, useTheme } from "../theme";
import { useUi } from "../ui";
import { PersonPicker } from "./PeopleParts";

// A prayer (ADR 0042): what you're praying for, who for, and once it's answered, how.
// Answered, not done: there is no tick and nothing is ever overdue. Marking one answered
// asks how, in a line, so a year from now memo can remind you.
export function PrayerSheet({
  api,
  existing,
  onClose,
  onSaved,
  onOpenNote,
}: {
  api: Api;
  existing?: Prayer;
  onClose: () => void;
  onSaved: () => void;
  onOpenNote?: (captureId: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const { width, height } = useWindowDimensions();
  const keyboard = useKeyboardHeight();
  const [title, setTitle] = useState(existing?.title ?? "");
  const [notes, setNotes] = useState(existing?.notes ?? "");
  const [answer, setAnswer] = useState(existing?.answer ?? "");
  const [person, setPerson] = useState<{ id: string; name: string } | null>(
    existing?.person_id ? { id: existing.person_id, name: existing.person_name ?? "Someone" } : null,
  );
  const [picking, setPicking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const answered = !!existing?.answered_at;

  const dirty =
    title.trim() !== (existing?.title ?? "").trim() ||
    notes.trim() !== (existing?.notes ?? "").trim() ||
    answer.trim() !== (existing?.answer ?? "").trim() ||
    (person?.id ?? null) !== (existing?.person_id ?? null);

  const close = () => {
    if (busy) return;
    if (!dirty) onClose();
    else confirmDiscard(existing ? "Discard your changes?" : "Discard this prayer?", "Discard", onClose);
  };

  const run = async (work: () => Promise<unknown>, after?: () => void) => {
    setBusy(true);
    setError(null);
    try {
      await work();
      after?.();
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };

  const save = () => {
    const t = title.trim();
    if (!t || busy) return;
    void run(async () => {
      if (existing) {
        await api.updatePrayer(existing.id, {
          title: t,
          notes: notes.trim() || null,
          person_id: person?.id ?? null,
          ...(answered ? { answer: answer.trim() || null } : {}),
        });
      } else {
        await api.createPrayer({ title: t, notes: notes.trim() || null, person_id: person?.id ?? null });
      }
      haptic.done();
    });
  };

  // How it was answered, in the user's words, then a quiet swarm: this is the moment
  // memo exists to remember.
  const markAnswered = async () => {
    if (!existing || busy) return;
    const how = await ask({
      title: "How was it answered?",
      message: "A line is enough. memo will remind you of it.",
      action: "Answered",
      allowEmpty: true,
    });
    if (how === null) return; // cancelled
    void run(
      () => api.updatePrayer(existing.id, { answered: true, answer: how }),
      () => {
        haptic.done();
        celebrate(width / 2, height * 0.5, true);
      },
    );
  };

  const stillPraying = () => {
    if (!existing || busy) return;
    void run(() => api.updatePrayer(existing.id, { answered: false }));
  };

  const remove = () => {
    if (!existing || busy) return;
    confirmDelete("Remove this prayer?", () => void run(() => api.updatePrayer(existing.id, { removed: true })));
  };

  return (
    <Modal
      visible
      animationType="slide"
      presentationStyle="pageSheet"
      allowSwipeDismissal={!dirty && !busy}
      onRequestClose={close}
    >
      <View style={[styles.sheet, { paddingBottom: 24 + keyboard }]}>
        <SheetHandle />
        <SheetHeader
          onClose={close}
          action={<Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!title.trim()} />}
        />
        <Text style={[ui.h2, styles.heading]}>{existing ? "Prayer" : "New prayer"}</Text>
        {answered && existing?.answered_at && (
          <Text style={styles.answeredOn}>
            <Ionicons name="checkmark-circle" size={15} color={colors.forest} /> Answered{" "}
            {formatDay(existing.answered_at.slice(0, 10), true)}
          </Text>
        )}
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 12 }}>
          <Text style={ui.label}>Praying for</Text>
          <TextInput
            style={styles.input}
            value={title}
            onChangeText={setTitle}
            placeholder="Tunde's mum's surgery"
            placeholderTextColor={colors.muted}
            autoFocus={!existing}
            maxLength={200}
          />

          <Text style={ui.label}>{"Who it's for"}</Text>
          {person ? (
            <View style={styles.personRow}>
              <Pressable onPress={() => setPicking(true)} style={styles.personChip} accessibilityRole="button">
                <Ionicons name="person-outline" size={16} color={colors.text} />
                <Text style={styles.personName}>{person.name}</Text>
              </Pressable>
              <Pressable onPress={() => setPerson(null)} hitSlop={10} accessibilityLabel="Not for anyone in particular">
                <Ionicons name="close-circle" size={20} color={colors.faint} />
              </Pressable>
            </View>
          ) : (
            <View style={styles.personRow}>
              <Pill label="Someone you know" icon="person-add-outline" onPress={() => setPicking(true)} />
            </View>
          )}

          <Text style={ui.label}>Notes</Text>
          <TextInput
            style={[styles.input, styles.multiline]}
            value={notes}
            onChangeText={setNotes}
            placeholder="Optional"
            placeholderTextColor={colors.muted}
            multiline
            maxLength={2000}
          />

          {answered && (
            <>
              <Text style={ui.label}>How it was answered</Text>
              <TextInput
                style={[styles.input, styles.multiline]}
                value={answer}
                onChangeText={setAnswer}
                placeholder="A line is enough"
                placeholderTextColor={colors.muted}
                multiline
                maxLength={2000}
              />
            </>
          )}

          {existing && (
            <SheetActions>
              {!answered && <SheetAction icon="checkmark-circle-outline" label="It was answered" busy={busy} onPress={markAnswered} />}
              {answered && <SheetAction icon="refresh-outline" label="Still praying" busy={busy} onPress={stillPraying} />}
              {existing.capture_id && onOpenNote && (
                <SheetAction
                  icon="document-text-outline"
                  label="Open the note"
                  onPress={() => {
                    onClose();
                    onOpenNote(existing.capture_id!);
                  }}
                />
              )}
              <SheetAction icon="trash-outline" label="Remove this prayer" danger busy={busy} onPress={remove} />
            </SheetActions>
          )}
        </ScrollView>
        {error && <Text style={[ui.danger, { marginTop: 12 }]}>{error}</Text>}
        {picking && (
          <PersonPicker
            api={api}
            title="Who is it for?"
            onClose={() => setPicking(false)}
            onPick={(p) => {
              setPicking(false);
              setPerson({ id: p.id, name: p.name });
            }}
          />
        )}
        <Dialogs />
        <LockCover />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((colors) => ({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  heading: { marginTop: 12, marginBottom: 6 },
  answeredOn: { color: colors.text, fontSize: 14, fontWeight: "600", marginBottom: 12 },
  input: {
    fontSize: 16,
    color: colors.text,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 16,
    backgroundColor: colors.surfaceAlt,
    marginBottom: 20,
  },
  multiline: { minHeight: 90, textAlignVertical: "top" },
  personRow: { flexDirection: "row", alignItems: "center", gap: 10, marginBottom: 20 },
  personChip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10,
    borderRadius: 999,
    backgroundColor: colors.surfaceAlt,
  },
  personName: { color: colors.text, fontSize: 15, fontWeight: "600" },
}));
