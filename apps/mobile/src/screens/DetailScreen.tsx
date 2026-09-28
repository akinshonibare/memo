import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, ScrollView, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, type CaptureDetail, type Derivation } from "../api";
import { AskBar, IconCircle, Pill, confirmDelete } from "../components";
import { ACTIVE_STATUSES, formatClock, formatDue, formatStamp, formatWhen, headline, statusLabel } from "../format";
import { playUrl, stopPlayback, usePlayingId } from "../playback";
import { colors, ui } from "../ui";

const POLL_MS = 3000;

// Extraction is never silent in either direction (issue #44): the processing state is
// visible, every derivation is one tap to confirm or reject, and a failure shows its
// reason with a retry.
export function DetailScreen({
  api,
  id,
  onBack,
  onAsk,
  onChanged,
}: {
  api: Api;
  id: string;
  onBack: () => void;
  onAsk: () => void;
  onChanged: () => void;
}) {
  const insets = useSafeAreaInsets();
  const [capture, setCapture] = useState<CaptureDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setCapture(await api.getCapture(id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, id]);

  const active = capture ? ACTIVE_STATUSES.has(capture.status) : true;
  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = active ? setInterval(load, POLL_MS) : null;
    return () => {
      clearTimeout(first);
      if (timer) clearInterval(timer);
    };
  }, [load, active]);

  const review = async (d: Derivation, status: "confirmed" | "rejected") => {
    setBusy(d.id);
    try {
      await api.reviewDerivation(d.id, { status });
      await load();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const retry = async () => {
    if (!capture) return;
    setBusy("retry");
    try {
      await api.retryCapture(capture.id);
      await load();
      onChanged();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const playingId = usePlayingId();
  const togglePlay = async () => {
    if (!capture) return;
    if (playingId === capture.id) {
      stopPlayback();
      return;
    }
    try {
      const audio = await api.getAudio(capture.id);
      await playUrl(capture.id, audio.url);
    } catch (e) {
      setError(`Couldn't play: ${String(e)}`);
    }
  };
  const remove = () =>
    confirmDelete("Delete this note? Everything extracted from it goes too.", async () => {
      if (!capture) return;
      if (playingId === capture.id) stopPlayback();
      try {
        await api.deleteCapture(capture.id);
        onChanged();
        onBack();
      } catch (e) {
        setError(`Couldn't delete: ${String(e)}`);
      }
    });

  const pending = capture?.derivations.filter((d) => d.status === "pending_review") ?? [];
  const settled = capture?.derivations.filter((d) => d.status !== "pending_review") ?? [];

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" onPress={onBack} />
        <View style={{ flex: 1 }} />
        {capture?.kind === "audio" && (
          <IconCircle name={playingId === capture.id ? "stop" : "play"} onPress={togglePlay} />
        )}
        {capture?.status === "failed" && (
          <Pill label="Retry" tone="light" onPress={retry} busy={busy === "retry"} />
        )}
        {capture && <IconCircle name="ellipsis-horizontal" onPress={remove} />}
      </View>
      <ScrollView
        contentContainerStyle={[ui.pad, { paddingBottom: 140 + insets.bottom }]}
        keyboardShouldPersistTaps="handled"
      >
        {capture && (
          <>
            <Text style={ui.meta}>
              {formatStamp(capture.created_at, true)}
              {capture.duration_ms != null ? ` · ${formatClock(capture.duration_ms)}` : ""}
            </Text>
            <Text style={[ui.h2, { marginTop: 6 }]}>{headline(capture).title}</Text>
            {headline(capture).rest && (
              <Text style={[ui.body, { marginTop: 14 }]}>{headline(capture).rest}</Text>
            )}
          </>
        )}
        {error && <Text style={[ui.danger, { marginTop: 16 }]}>{error}</Text>}

        {capture && ACTIVE_STATUSES.has(capture.status) && (
          <View style={styles.processing}>
            <ActivityIndicator color={colors.muted} />
            <Text style={[ui.meta, { marginTop: 10 }]}>{statusLabel(capture.status)}</Text>
          </View>
        )}

        {capture?.status === "failed" && (
          <View style={styles.failure}>
            <Text style={ui.danger}>{"Couldn't process this note."}</Text>
            <Text style={[ui.bodyMuted, { marginTop: 4 }]}>{capture.failure_reason}</Text>
          </View>
        )}

        {pending.length > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>To review</Text>
            {pending.map((d) => (
              <DerivationCard key={d.id} d={d} busy={busy === d.id} onReview={review} />
            ))}
          </View>
        )}
        {settled.length > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>Extracted</Text>
            {settled.map((d) => (
              <DerivationCard key={d.id} d={d} busy={false} onReview={review} />
            ))}
          </View>
        )}
        {capture?.status === "ready" && capture.derivations.length === 0 && (
          <Text style={[ui.bodyMuted, { marginTop: 28 }]}>Nothing to extract from this one.</Text>
        )}
      </ScrollView>
      <View style={[styles.bottom, { paddingBottom: Math.max(insets.bottom, 16) }]}>
        <AskBar onPress={onAsk} />
      </View>
    </View>
  );
}

function DerivationCard({
  d,
  busy,
  onReview,
}: {
  d: Derivation;
  busy: boolean;
  onReview: (d: Derivation, status: "confirmed" | "rejected") => void;
}) {
  const p = d.payload;
  const when =
    "due" in p && p.due
      ? `Due ${formatDue(String(p.due))}`
      : "starts_at" in p && p.starts_at
        ? formatWhen(String(p.starts_at))
        : null;
  const statusText =
    d.status === "pending_review"
      ? d.confidence != null
        ? `${Math.round(d.confidence * 100)}% sure`
        : null
      : d.status.replace("_", " ");
  return (
    <View style={styles.card}>
      <Text style={ui.meta}>
        {d.kind}
        {statusText ? ` · ${statusText}` : ""}
      </Text>
      <Text style={ui.rowTitle}>{String(p.title ?? "")}</Text>
      {when && <Text style={ui.bodyMuted}>{when}</Text>}
      {"summary" in p && p.summary ? <Text style={ui.bodyMuted}>{String(p.summary)}</Text> : null}
      {"notes" in p && p.notes ? <Text style={ui.bodyMuted}>{String(p.notes)}</Text> : null}
      {d.status === "pending_review" && (
        <View style={styles.actions}>
          <Pill label="Confirm" tone="light" grow busy={busy} onPress={() => onReview(d, "confirmed")} />
          <Pill label="Reject" grow disabled={busy} onPress={() => onReview(d, "rejected")} />
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, marginBottom: 20 },
  processing: { alignItems: "center", paddingVertical: 56 },
  failure: {
    marginTop: 24,
    padding: 16,
    borderRadius: 18,
    backgroundColor: "#1f1214",
  },
  section: { marginTop: 32 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    gap: 2,
  },
  actions: { flexDirection: "row", gap: 10, marginTop: 14 },
  bottom: {
    position: "absolute",
    left: 20,
    right: 20,
    bottom: 0,
    flexDirection: "row",
  },
});
