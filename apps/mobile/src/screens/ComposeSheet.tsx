import { useState } from "react";
import { Modal, Text, TextInput, View } from "react-native";
import { Api, ApiError } from "../api";
import { confirmDiscard, Pill, SheetHandle, SheetHeader } from "../components";
import { useKeyboardHeight } from "../keyboard";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { haptic } from "../haptics";
import { captureOrQueue } from "../outbox";
import { LockCover } from "../lock";

export function ComposeSheet({
  api,
  initialText,
  onClose,
  onSaved,
}: {
  api: Api;
  initialText?: string; // from the share sheet: a link or text to annotate

  onClose: () => void;
  onSaved: () => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [text, setText] = useState(initialText ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyboard = useKeyboardHeight();
  // Something typed (beyond what the share sheet brought in) is asked about before
  // it's thrown away, by the close button or a swipe down.
  const dirty = !!text.trim() && text.trim() !== (initialText ?? "").trim();
  const close = () => {
    if (busy) return;
    if (!dirty) onClose();
    else confirmDiscard("Discard this note?", "Discard", onClose);
  };

  const save = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      await captureOrQueue(api, { kind: "text", text: body }); // offline: kept, synced later
      haptic.done();
      onSaved();
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
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
          action={<Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!text.trim()} />}
        />
        <Text style={[ui.h2, styles.title]}>New note</Text>
        <TextInput
          style={styles.input}
          placeholder="What's on your mind?"
          placeholderTextColor={colors.muted}
          value={text}
          onChangeText={setText}
          multiline
          autoFocus
          scrollEnabled
        />
        {error && <Text style={[ui.danger, { marginBottom: 12 }]}>{error}</Text>}
        <LockCover />
      </View>
    </Modal>
  );
}

const useStyles = makeStyles((colors) => ({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24, paddingBottom: 24 },
  title: { marginTop: 12, marginBottom: 12 },
  input: {
    flex: 1,
    color: colors.text,
    fontSize: 17,
    lineHeight: 24,
    textAlignVertical: "top",
  },
}));
