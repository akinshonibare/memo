import { useState } from "react";
import { Pressable, Text, View } from "react-native";
import type { Update } from "../api";
import { Pill } from "../components";
import { haptic } from "../haptics";
import { useUi } from "../ui";
import { makeStyles } from "../theme";

// A note that may have changed something you wrote earlier (ADR 0048): it answers a
// prayer, or carries on an idea. Asked, never assumed: the quote is the note's own
// words, and it opens the note.
export function UpdateCard({
  update,
  onDecide,
  onOpenNote,
}: {
  update: Update;
  onDecide: (yes: boolean) => Promise<void>;
  onOpenNote: (captureId: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const [busy, setBusy] = useState<"yes" | "no" | null>(null);
  const prayer = update.relation === "answers";
  const decide = async (yes: boolean) => {
    haptic.tap();
    setBusy(yes ? "yes" : "no");
    try {
      await onDecide(yes);
      if (yes) haptic.done();
    } finally {
      setBusy(null);
    }
  };
  const when = new Date(update.written_at).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  return (
    <View style={styles.card}>
      <Text style={ui.meta}>{prayer ? "Answered?" : "Carries on a highlight?"}</Text>
      <Text style={ui.rowTitle}>
        {update.target.title}
        {update.target.person_name ? <Text style={ui.bodyMuted}> · {update.target.person_name}</Text> : null}
      </Text>
      <Pressable
        disabled={!update.capture_id}
        onPress={() => update.capture_id && onOpenNote(update.capture_id)}
        style={({ pressed }) => [styles.quote, pressed && { opacity: 0.6 }]}
        accessibilityRole="link"
        accessibilityHint="Opens the note"
      >
        <Text style={styles.quoteText}>“{update.quote}”</Text>
        <Text style={ui.meta}>Your note, {when}</Text>
      </Pressable>
      <View style={styles.actions}>
        <Pill
          label={prayer ? "Yes, answered" : "Yes, add to it"}
          icon="checkmark"
          tone="light"
          busy={busy === "yes"}
          disabled={busy !== null}
          onPress={() => void decide(true)}
        />
        <Pill
          label={prayer ? "Not yet" : "No"}
          busy={busy === "no"}
          disabled={busy !== null}
          onPress={() => void decide(false)}
        />
      </View>
    </View>
  );
}

// What an idea has been carried on by, under the idea: the notes you said yes to.
export function IdeaThread({ updates, onOpenNote }: { updates: Update[]; onOpenNote: (captureId: string) => void }) {
  const ui = useUi();
  const styles = useStyles();
  if (updates.length === 0) return null;
  // Oldest first: a thread reads in the order it was written.
  const ordered = [...updates].sort((a, b) => a.written_at.localeCompare(b.written_at));
  return (
    <View style={styles.thread}>
      {ordered.map((u) => (
        <Pressable
          key={u.id}
          disabled={!u.capture_id}
          onPress={() => u.capture_id && onOpenNote(u.capture_id)}
          style={({ pressed }) => [styles.threadRow, pressed && { opacity: 0.6 }]}
        >
          <Text style={ui.meta}>
            {new Date(u.written_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
          </Text>
          <Text style={[ui.bodyMuted, { flex: 1 }]} numberOfLines={2}>
            {u.quote}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  card: { backgroundColor: colors.surface, borderRadius: 16, padding: 14, marginBottom: 10, gap: 6 },
  quote: {
    borderLeftWidth: 2,
    borderLeftColor: colors.faint,
    paddingLeft: 10,
    paddingVertical: 2,
    marginTop: 4,
    gap: 2,
  },
  quoteText: { color: colors.text, fontStyle: "italic", fontSize: 15, lineHeight: 21 },
  actions: { flexDirection: "row", gap: 8, marginTop: 6 },
  thread: { marginTop: 8, gap: 6, borderTopWidth: 1, borderTopColor: colors.hairline, paddingTop: 8 },
  threadRow: { flexDirection: "row", gap: 8, alignItems: "baseline" },
}));
