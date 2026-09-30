import { Ionicons } from "@expo/vector-icons";
import { useState } from "react";
import { Image, Modal, Pressable, Text, TextInput, View } from "react-native";
import { Api, ApiError, type Space } from "../api";
import { confirmDiscard, IconCircle, Pill, SheetHandle, SheetHeader } from "../components";
import { useKeyboardHeight } from "../keyboard";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { haptic } from "../haptics";
import { captureOrQueue } from "../outbox";
import { pickPhoto } from "../photo";
import { Dialogs } from "../dialogs";
import { LockCover } from "../lock";

// A written note, or a photo with your words beside it (ADR 0056): the words go first
// in the note, and memo describes the photo in their light. A photo alone is fine too.
export function ComposeSheet({
  api,
  initialText,
  initialPhoto,
  space = "main",
  onClose,
  onSaved,
}: {
  api: Api;
  initialText?: string; // from the share sheet: a link or text to annotate
  initialPhoto?: string; // a photo already picked (from + or the share sheet), resized
  space?: Space; // written from the Faith screen: a faith note (ADR 0042)

  onClose: () => void;
  onSaved: (captureId: string | null) => void; // null: kept offline, sent later
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const [text, setText] = useState(initialText ?? "");
  const [photo, setPhoto] = useState<string | null>(initialPhoto ?? null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const keyboard = useKeyboardHeight();
  // Something typed or attached (beyond what came in) is asked about before it's thrown
  // away, by the close button or a swipe down.
  const dirty =
    (!!text.trim() && text.trim() !== (initialText ?? "").trim()) || (!!photo && photo !== (initialPhoto ?? null));
  const close = () => {
    if (busy) return;
    if (!dirty) onClose();
    else confirmDiscard("Discard this note?", "Discard", onClose);
  };

  const attach = async (source: "camera" | "library") => {
    try {
      const uri = await pickPhoto(source);
      if (uri) {
        haptic.tap();
        setPhoto(uri);
        setError(null);
      }
    } catch (e) {
      setError(String(e instanceof Error ? e.message : e));
    }
  };

  const save = async () => {
    const body = text.trim();
    if ((!body && !photo) || busy) return;
    setBusy(true);
    try {
      // offline: kept, synced later, words and photo together
      const id = photo
        ? await captureOrQueue(api, { kind: "image", file: photo, text: body || undefined, space })
        : await captureOrQueue(api, { kind: "text", text: body, space });
      haptic.done();
      onSaved(id);
      onClose();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      setBusy(false);
    }
  };

  const placeholder = photo
    ? "Add a few words about it, if you like."
    : space === "faith"
      ? "What did you read? What stood out? Name the passage and memo will keep it."
      : "What's on your mind?";

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
          action={<Pill label="Save" tone="light" onPress={save} busy={busy} disabled={!text.trim() && !photo} />}
        />
        <Text style={[ui.h2, styles.title]}>{space === "faith" ? "New reflection" : "New note"}</Text>
        {photo ? (
          <View style={styles.photoWrap}>
            <Image source={{ uri: photo }} style={styles.photo} resizeMode="cover" accessibilityLabel="Attached photo" />
            <Pressable
              onPress={() => setPhoto(null)}
              hitSlop={10}
              style={styles.remove}
              accessibilityRole="button"
              accessibilityLabel="Remove the photo"
            >
              <Ionicons name="close" size={16} color={colors.text} />
            </Pressable>
          </View>
        ) : null}
        <TextInput
          style={styles.input}
          placeholder={placeholder}
          placeholderTextColor={colors.muted}
          value={text}
          onChangeText={setText}
          multiline
          autoFocus
          scrollEnabled
        />
        {error && <Text style={[ui.danger, { marginBottom: 12 }]}>{error}</Text>}
        {!photo && (
          <View style={styles.tools}>
            <IconCircle name="camera-outline" size={44} onPress={() => void attach("camera")} accessibilityLabel="Take a photo" />
            <IconCircle
              name="images-outline"
              size={44}
              onPress={() => void attach("library")}
              accessibilityLabel="Add a photo from your library"
            />
          </View>
        )}
        <Dialogs />
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
  photoWrap: { marginBottom: 14, alignSelf: "flex-start" },
  photo: { width: 160, height: 160, borderRadius: 16, backgroundColor: colors.surfaceAlt },
  remove: {
    position: "absolute",
    top: 8,
    right: 8,
    width: 28,
    height: 28,
    borderRadius: 14,
    backgroundColor: colors.surfaceAlt,
    alignItems: "center",
    justifyContent: "center",
  },
  tools: { flexDirection: "row", gap: 10, paddingTop: 8 },
}));
