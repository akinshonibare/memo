import { useState } from "react";
import { Modal, StyleSheet, Text, TextInput, View } from "react-native";
import { Api, ApiError, type Capture } from "../api";
import { IconCircle, Pill, SheetHandle } from "../components";
import { useKeyboardHeight } from "../keyboard";
import { colors, ui } from "../ui";

export function ComposeSheet({
  api,
  onClose,
  onSaved,
}: {
  api: Api;
  onClose: () => void;
  onSaved: (capture: Capture) => void;
}) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyboard = useKeyboardHeight();

  const save = async () => {
    const body = text.trim();
    if (!body || busy) return;
    setBusy(true);
    try {
      const saved = await api.createTextCapture(body);
      onSaved(saved);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
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
        <View style={styles.actions}>
          <Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!text.trim()} />
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  sheet: { flex: 1, backgroundColor: colors.surface, paddingHorizontal: 24, paddingBottom: 24 },
  top: { flexDirection: "row", alignItems: "center", marginTop: 8 },
  title: { marginTop: 12, marginBottom: 12 },
  input: {
    flex: 1,
    color: colors.text,
    fontSize: 17,
    lineHeight: 24,
    textAlignVertical: "top",
  },
  actions: { flexDirection: "row", justifyContent: "flex-end", paddingTop: 12 },
});
