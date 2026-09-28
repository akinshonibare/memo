import { useCallback, useEffect, useState } from "react";
import { FlatList, Pressable, StyleSheet, Text, View } from "react-native";
import { Api, type Capture, type CaptureDetail, type Derivation } from "../api";
import { colors, ui } from "../ui";

const POLL_MS = 3000;
const ACTIVE = new Set(["pending", "transcribing", "extracting", "embedding"]);

// Extraction is never silent, in either direction (issue #44): every capture shows
// its processing state, every derivation is one tap to confirm or reject, and a
// failed capture shows its reason with a retry button.
export function ReviewScreen({ api }: { api: Api }) {
  const [captures, setCaptures] = useState<Capture[]>([]);
  const [open, setOpen] = useState<CaptureDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      const page = await api.listCaptures(50);
      setCaptures(page.items);
      setError(null);
      if (open) setOpen(await api.getCapture(open.id));
    } catch (e) {
      setError(String(e));
    }
  }, [api, open]);

  useEffect(() => {
    refresh();
    const anyActive = captures.some((c) => ACTIVE.has(c.status));
    const timer = setInterval(refresh, anyActive ? POLL_MS : POLL_MS * 4);
    return () => clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [api, captures.some((c) => ACTIVE.has(c.status)), open?.id]);

  const review = async (d: Derivation, status: "confirmed" | "rejected") => {
    await api.reviewDerivation(d.id, { status });
    if (open) setOpen(await api.getCapture(open.id));
  };

  const retry = async (capture: Capture) => {
    await api.retryCapture(capture.id);
    await refresh();
  };

  if (open) {
    return (
      <View style={ui.screen}>
        <Pressable onPress={() => setOpen(null)}>
          <Text style={ui.link}>‹ All captures</Text>
        </Pressable>
        <Text style={ui.cardTitle}>{open.text ?? "Voice note"}</Text>
        <Text style={ui.cardMeta}>{open.status}</Text>
        {open.status === "failed" && (
          <View style={ui.card}>
            <Text style={styles.failure}>Couldn't process this — {open.failure_reason}</Text>
            <Pressable style={ui.button} onPress={() => retry(open)}>
              <Text style={ui.buttonText}>Retry</Text>
            </Pressable>
          </View>
        )}
        <FlatList
          data={open.derivations}
          keyExtractor={(d) => d.id}
          renderItem={({ item }) => (
            <View style={ui.card}>
              <Text style={ui.cardMeta}>
                {item.kind} · {item.status}
                {item.confidence != null ? ` · ${Math.round(item.confidence * 100)}%` : ""}
              </Text>
              <Text style={ui.cardTitle}>{String(item.payload.title ?? "")}</Text>
              {"due" in item.payload && item.payload.due ? (
                <Text style={ui.cardMeta}>due {String(item.payload.due)}</Text>
              ) : null}
              {"starts_at" in item.payload && item.payload.starts_at ? (
                <Text style={ui.cardMeta}>{String(item.payload.starts_at)}</Text>
              ) : null}
              {"summary" in item.payload && item.payload.summary ? (
                <Text style={ui.cardBody}>{String(item.payload.summary)}</Text>
              ) : null}
              {item.status === "pending_review" && (
                <View style={styles.row}>
                  <Pressable style={ui.button} onPress={() => review(item, "confirmed")}>
                    <Text style={ui.buttonText}>Confirm</Text>
                  </Pressable>
                  <Pressable style={[ui.button, styles.reject]} onPress={() => review(item, "rejected")}>
                    <Text style={ui.buttonText}>Reject</Text>
                  </Pressable>
                </View>
              )}
            </View>
          )}
          ListEmptyComponent={
            <Text style={ui.empty}>
              {ACTIVE.has(open.status) ? "Still processing…" : "Nothing was extracted."}
            </Text>
          }
        />
      </View>
    );
  }

  return (
    <View style={ui.screen}>
      {error && <Text style={styles.failure}>{error}</Text>}
      <FlatList
        data={captures}
        keyExtractor={(c) => c.id}
        renderItem={({ item }) => (
          <Pressable style={ui.card} onPress={async () => setOpen(await api.getCapture(item.id))}>
            <Text style={ui.cardTitle} numberOfLines={2}>
              {item.text ?? "Voice note"}
            </Text>
            <Text style={[ui.cardMeta, item.status === "failed" && styles.failure]}>
              {item.status === "failed" ? "couldn't process — tap to retry" : item.status}
            </Text>
          </Pressable>
        )}
        ListEmptyComponent={<Text style={ui.empty}>No captures yet.</Text>}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 8, marginTop: 8 },
  reject: { backgroundColor: colors.danger },
  failure: { color: colors.danger, marginBottom: 8 },
});
