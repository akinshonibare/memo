import DateTimePicker from "@react-native-community/datetimepicker";
import { useState } from "react";
import { Modal, ScrollView, Text, TextInput, View } from "react-native";
import { Api, ApiError, type Derivation } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { toIsoDate, toLocalIso } from "../format";
import { useKeyboardHeight } from "../keyboard";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { haptic } from "../haptics";
import { LockCover } from "../lock";

type Kind = Derivation["kind"];

// Edit what the extractor produced, or add what it missed (issue #44, handoff item 3).
// The two are different signals to the evals: a PATCH is a precision correction, a POST
// is a recall failure recorded as `user_created`. The form is the same either way.
export function DerivationSheet({
  api,
  captureId,
  kind,
  existing,
  initialDate,
  onOpenNote,
  onClose,
  onSaved,
}: {
  api: Api;
  // To add to a note. null adds a standalone item (the Planner), or edits one.
  captureId: string | null;
  kind: Kind;
  existing?: Derivation;
  initialDate?: Date; // the Planner's selected day, for a new todo or event
  onOpenNote?: (captureId: string) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors, scheme } = useTheme();
  const p = existing?.payload ?? {};
  const str = (key: string) => (typeof p[key] === "string" ? (p[key] as string) : "");

  const [title, setTitle] = useState(str("title"));
  const [body, setBody] = useState(kind === "idea" ? str("summary") : str("notes"));
  const [location, setLocation] = useState(str("location"));
  const [due, setDue] = useState<Date | null>(
    str("due") ? new Date(`${str("due")}T00:00:00`) : existing ? null : (initialDate ?? null),
  );
  const [startsAt, setStartsAt] = useState<Date>(() => initialStart(str("starts_at"), initialDate));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyboard = useKeyboardHeight();

  const payload = (): object => {
    const t = title.trim();
    if (kind === "todo") return { title: t, due: due ? toIsoDate(due) : null, notes: body.trim() || null };
    if (kind === "idea") return { title: t, summary: body.trim() };
    return {
      title: t,
      starts_at: toLocalIso(startsAt),
      ends_at: shiftedEnd(str("starts_at"), str("ends_at"), startsAt),
      location: location.trim() || null,
    };
  };

  const canSave = !!title.trim() && (kind !== "idea" || !!body.trim());

  const save = async () => {
    if (!canSave || busy) return;
    setBusy(true);
    try {
      if (existing) await api.reviewDerivation(existing.id, { payload: payload() });
      else if (captureId) await api.addDerivation(captureId, kind, payload());
      else await api.createDerivation(kind, payload());
      haptic.done();
      onSaved();
      onClose();
    } catch (e) {
      setError(describe(e));
      setBusy(false);
    }
  };

  // There is no delete for an item: rejecting it removes it everywhere it shows (Today,
  // the Planner, Ask) and keeps the "this was wrong" signal for the evals.
  const remove = async () => {
    if (!existing || busy) return;
    setBusy(true);
    try {
      await api.reviewDerivation(existing.id, { status: "rejected" });
      onSaved();
      onClose();
    } catch (e) {
      setError(describe(e));
      setBusy(false);
    }
  };
  const noteId = existing?.capture_id ?? null;

  return (
    <Modal visible animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={[styles.sheet, { paddingBottom: 24 + keyboard }]}>
        <SheetHandle />
        <View style={styles.top}>
          <View style={{ flex: 1 }} />
          <IconCircle name="close" onPress={onClose} />
        </View>
        <Text style={[ui.h2, styles.heading]}>
          {existing ? `Edit ${kind}` : `Add ${kind === "todo" ? "a" : "an"} ${kind}`}
        </Text>
        <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingBottom: 12 }}>
          <Text style={ui.label}>Title</Text>
          <TextInput
            style={styles.input}
            value={title}
            onChangeText={setTitle}
            placeholder={kind === "todo" ? "Call Tunde" : kind === "event" ? "Dentist" : "The idea, in a line"}
            placeholderTextColor={colors.muted}
            autoFocus={!existing}
            maxLength={200}
          />

          {kind === "todo" && (
            <>
              <Text style={ui.label}>Due</Text>
              {due ? (
                <View style={styles.row}>
                  <DateTimePicker
                    value={due}
                    mode="date"
                    display="compact"
                    themeVariant={scheme}
                    accentColor={colors.text}
                    onValueChange={(_, d) => setDue(d)}
                  />
                  <View style={{ flex: 1 }} />
                  <Pill label="No date" onPress={() => setDue(null)} />
                </View>
              ) : (
                <View style={styles.row}>
                  <Pill label="Add a due date" icon="calendar-outline" onPress={() => setDue(startOfToday())} />
                </View>
              )}
            </>
          )}

          {kind === "event" && (
            <>
              <Text style={ui.label}>Starts</Text>
              <View style={styles.row}>
                <DateTimePicker
                  value={startsAt}
                  mode="datetime"
                  display="compact"
                  themeVariant={scheme}
                  accentColor={colors.text}
                  minuteInterval={5}
                  onValueChange={(_, d) => setStartsAt(d)}
                />
              </View>
              <Text style={ui.label}>Where</Text>
              <TextInput
                style={styles.input}
                value={location}
                onChangeText={setLocation}
                placeholder="Optional"
                placeholderTextColor={colors.muted}
                maxLength={200}
              />
            </>
          )}

          {kind !== "event" && (
            <>
              <Text style={ui.label}>{kind === "idea" ? "Summary" : "Notes"}</Text>
              <TextInput
                style={[styles.input, styles.multiline]}
                value={body}
                onChangeText={setBody}
                placeholder={kind === "idea" ? "What's worth coming back to" : "Optional"}
                placeholderTextColor={colors.muted}
                multiline
                maxLength={kind === "idea" ? 1000 : 2000}
              />
            </>
          )}
        </ScrollView>
        {error && <Text style={[ui.danger, { marginBottom: 12 }]}>{error}</Text>}
        <View style={styles.actions}>
          {existing && <Pill label="Remove" tone="danger" onPress={remove} disabled={busy} />}
          {noteId && onOpenNote && (
            <Pill
              label="Open note"
              onPress={() => {
                onClose();
                onOpenNote(noteId);
              }}
            />
          )}
          <View style={{ flex: 1 }} />
          <Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!canSave} />
        </View>
        <LockCover />
      </View>
    </Modal>
  );
}

function startOfToday(): Date {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
}

// A new event defaults to the next whole hour, which is almost never right but is
// always a plausible starting point for the picker.
function initialStart(iso: string, day?: Date): Date {
  const parsed = iso ? new Date(iso) : null;
  if (parsed && !Number.isNaN(parsed.getTime())) return parsed;
  const now = new Date();
  const d = day ? new Date(day) : new Date(now);
  d.setHours(now.getHours() + 1, 0, 0, 0);
  return d;
}

// The sheet doesn't edit an event's end, but it can't leave one behind the new start
// either (the server rejects ends_at < starts_at): moving the start moves the end with it.
function shiftedEnd(oldStart: string, oldEnd: string, newStart: Date): string | null {
  if (!oldEnd) return null;
  const start = new Date(oldStart).getTime();
  const end = new Date(oldEnd).getTime();
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return toLocalIso(new Date(newStart.getTime() + (end - start)));
}

// A 422 carries [{loc, msg}] (memo.schemas.derivations.validation_detail).
function describe(e: unknown): string {
  if (!(e instanceof ApiError)) return String(e);
  try {
    const detail = JSON.parse(e.message).detail;
    if (Array.isArray(detail)) return detail.map((d: { msg: string }) => d.msg).join("\n");
    if (typeof detail === "string") return detail;
  } catch {
    // Not JSON: fall through to the raw message.
  }
  return e.message;
}

const useStyles = makeStyles((colors) => ({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  heading: { marginTop: 12, marginBottom: 20 },
  input: {
    fontSize: 16,
    color: colors.text,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 16,
    backgroundColor: colors.surfaceAlt,
    marginBottom: 20,
  },
  multiline: { minHeight: 110, textAlignVertical: "top" },
  row: { flexDirection: "row", alignItems: "center", marginBottom: 20 },
  actions: { flexDirection: "row", alignItems: "center", gap: 8, paddingTop: 12 },
}));
