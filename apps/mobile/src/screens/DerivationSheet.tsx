import DateTimePicker from "@react-native-community/datetimepicker";
import { useState } from "react";
import { Modal, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { Api, ApiError, type Derivation } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { toIsoDate, toLocalIso } from "../format";
import { useKeyboardHeight } from "../keyboard";
import { colors, ui } from "../ui";

type Kind = Derivation["kind"];

// Edit what the extractor produced, or add what it missed (issue #44, handoff item 3).
// The two are different signals to the evals: a PATCH is a precision correction, a POST
// is a recall failure recorded as `user_created`. The form is the same either way.
export function DerivationSheet({
  api,
  captureId,
  kind,
  existing,
  onClose,
  onSaved,
}: {
  api: Api;
  captureId: string | null; // only needed to add; null when editing a kept item
  kind: Kind;
  existing?: Derivation;
  onClose: () => void;
  onSaved: () => void;
}) {
  const p = existing?.payload ?? {};
  const str = (key: string) => (typeof p[key] === "string" ? (p[key] as string) : "");

  const [title, setTitle] = useState(str("title"));
  const [body, setBody] = useState(kind === "idea" ? str("summary") : str("notes"));
  const [location, setLocation] = useState(str("location"));
  const [due, setDue] = useState<Date | null>(str("due") ? new Date(`${str("due")}T00:00:00`) : null);
  const [startsAt, setStartsAt] = useState<Date>(() => initialStart(str("starts_at")));
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
      onSaved();
      onClose();
    } catch (e) {
      setError(describe(e));
      setBusy(false);
    }
  };

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
                    themeVariant="dark"
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
                  themeVariant="dark"
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
          <Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!canSave} />
        </View>
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
function initialStart(iso: string): Date {
  const parsed = iso ? new Date(iso) : null;
  if (parsed && !Number.isNaN(parsed.getTime())) return parsed;
  const d = new Date();
  d.setHours(d.getHours() + 1, 0, 0, 0);
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

const styles = StyleSheet.create({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  heading: { marginTop: 12, marginBottom: 20 },
  input: {
    fontSize: 17,
    color: colors.text,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 16,
    backgroundColor: colors.surfaceAlt,
    marginBottom: 20,
  },
  multiline: { minHeight: 110, textAlignVertical: "top" },
  row: { flexDirection: "row", alignItems: "center", marginBottom: 20 },
  actions: { flexDirection: "row", justifyContent: "flex-end", paddingTop: 12 },
});
