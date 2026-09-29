import { useCallback, useEffect, useState } from "react";
import {
  ActionSheetIOS,
  ActivityIndicator,
  Alert,
  Image,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, type Capture, type CaptureDetail, type Derivation } from "../api";
import { AskBar, IconCircle, Pill, confirmDelete } from "../components";
import { ACTIVE_STATUSES, formatClock, formatDue, formatStamp, formatWhen, headline, statusLabel } from "../format";
import { playUrl, stopPlayback, usePlayingId } from "../playback";
import { colors, ui } from "../ui";
import { DerivationSheet } from "./DerivationSheet";
import { MentionCard } from "./PeopleParts";
import { haptic } from "../haptics";

const KINDS: Derivation["kind"][] = ["todo", "event", "idea"];

// Pick what to add. iOS gets the native action sheet.
function chooseKind(onPick: (kind: Derivation["kind"]) => void) {
  const labels = ["A todo", "An event", "An idea"];
  if (Platform.OS === "ios") {
    ActionSheetIOS.showActionSheetWithOptions(
      { title: "What did it miss?", options: [...labels, "Cancel"], cancelButtonIndex: 3 },
      (index) => {
        if (index < 3) onPick(KINDS[index]);
      },
    );
    return;
  }
  Alert.alert("What did it miss?", undefined, [
    ...labels.map((text, i) => ({ text, onPress: () => onPick(KINDS[i]) })),
    { text: "Cancel", style: "cancel" as const },
  ]);
}

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
  onOpen,
  onOpenPerson,
}: {
  api: Api;
  id: string;
  onBack: () => void;
  onAsk: () => void;
  onChanged: () => void;
  onOpen: (id: string) => void;
  onOpenPerson: (id: string) => void;
}) {
  const insets = useSafeAreaInsets();

  // Notes about the same thing (ADR 0028), once this one is indexed. Quiet by design:
  // the server shows nothing rather than something weakly related.
  const [related, setRelated] = useState<{ capture: Capture; similarity: number }[]>([]);
  const [capture, setCapture] = useState<CaptureDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editing, setEditing] = useState<
    { kind: Derivation["kind"]; existing?: Derivation } | null
  >(null);

  const load = useCallback(async () => {
    try {
      setCapture(await api.getCapture(id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, id]);

  // The photo comes straight from the bucket on a short-lived URL; fetched once per note.
  const [photo, setPhoto] = useState<{ url: string; ratio: number } | null>(null);
  const isImage = capture?.kind === "image";
  useEffect(() => {
    if (!isImage) return;
    let live = true;
    api
      .getMedia(id)
      .then(({ url }) =>
        Image.getSize(url, (w, h) => live && setPhoto({ url, ratio: w / h }), () => live && setPhoto({ url, ratio: 4 / 3 })),
      )
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [api, id, isImage]);

  const active = capture ? ACTIVE_STATUSES.has(capture.status) : true;
  useEffect(() => {
    const first = setTimeout(load, 0);
    const timer = active ? setInterval(load, POLL_MS) : null;
    return () => {
      clearTimeout(first);
      if (timer) clearInterval(timer);
    };
  }, [load, active]);

  const ready = capture?.status === "ready";
  useEffect(() => {
    if (!ready) return;
    let live = true;
    api
      .related(id)
      .then((rows) => live && setRelated(rows))
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [api, id, ready]);

  const review = async (d: Derivation, status: "confirmed" | "rejected") => {
    haptic.pick();
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
    confirmDelete("Delete this note? Its todos, events and ideas are kept.", async () => {
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

  // Who the note is about (ADR 0029): linked people as chips, and "which one?" cards
  // for a name memo could not settle on its own.
  const people = capture?.people ?? [];
  const linked = people.filter((m) => m.person);
  const asking = people.filter((m) => !m.person);
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
        {photo && (
          <Image
            source={{ uri: photo.url }}
            style={[styles.photo, { aspectRatio: Math.max(photo.ratio, 0.6) }]}
            resizeMode="cover"
            accessibilityLabel="The photo this note came from"
          />
        )}
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

        {people.length > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>People</Text>
            <View style={styles.chips}>
              {linked.map((m) => (
                <MentionCard
                  key={m.id}
                  api={api}
                  mention={m}
                  onChanged={() => {
                    load();
                    onChanged();
                  }}
                  onOpenPerson={onOpenPerson}
                />
              ))}
            </View>
            {asking.map((m) => (
              <MentionCard
                key={m.id}
                api={api}
                mention={m}
                onChanged={() => {
                  load();
                  onChanged();
                }}
                onOpenPerson={onOpenPerson}
              />
            ))}
          </View>
        )}
        {pending.length > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>To review</Text>
            {pending.map((d) => (
              <DerivationCard
                key={d.id}
                d={d}
                busy={busy === d.id}
                onReview={review}
                onEdit={() => setEditing({ kind: d.kind, existing: d })}
              />
            ))}
          </View>
        )}
        {settled.length > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>Extracted</Text>
            {settled.map((d) => (
              <DerivationCard
                key={d.id}
                d={d}
                busy={false}
                onReview={review}
                onEdit={() => setEditing({ kind: d.kind, existing: d })}
              />
            ))}
          </View>
        )}
        {capture?.status === "ready" && capture.derivations.length === 0 && (
          <Text style={[ui.bodyMuted, { marginTop: 28 }]}>
            {capture.text?.trim()
              ? "Nothing to extract from this one."
              : capture.kind === "audio"
                ? "Nothing was said in this recording."
                : "This note is empty."}
          </Text>
        )}
        {related.length > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>Related notes</Text>
            {related.map(({ capture: other }) => (
              <Pressable
                key={other.id}
                onPress={() => onOpen(other.id)}
                style={({ pressed }) => [styles.relatedRow, pressed && { opacity: 0.6 }]}
              >
                <Text style={ui.meta}>{formatStamp(other.created_at)}</Text>
                <Text style={styles.relatedTitle} numberOfLines={2}>
                  {headline(other).title}
                </Text>
              </Pressable>
            ))}
          </View>
        )}
        {capture?.status === "ready" && (
          <View style={styles.add}>
            <Pill
              label="Add what it missed"
              icon="add"
              onPress={() => chooseKind((kind) => setEditing({ kind }))}
            />
          </View>
        )}
      </ScrollView>
      <View style={[styles.bottom, { paddingBottom: Math.max(insets.bottom, 16) }]}>
        <AskBar onPress={onAsk} />
      </View>
      {capture && editing && (
        <DerivationSheet
          api={api}
          captureId={capture.id}
          kind={editing.kind}
          existing={editing.existing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            load();
            onChanged();
          }}
        />
      )}
    </View>
  );
}

function DerivationCard({
  d,
  busy,
  onReview,
  onEdit,
}: {
  d: Derivation;
  busy: boolean;
  onReview: (d: Derivation, status: "confirmed" | "rejected") => void;
  onEdit: () => void;
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
      : d.status === "user_created"
        ? "added by you"
        : d.status === "accepted"
          ? "kept automatically"
          : d.status;
  // A settled card is tapped to edit it; a rejected one is left alone. A card awaiting
  // review gets Edit alongside Confirm and Reject (saving an edit also confirms it).
  const editable = d.status === "confirmed" || d.status === "user_created" || d.status === "accepted";
  return (
    <Pressable
      onPress={editable ? onEdit : undefined}
      disabled={!editable}
      style={({ pressed }) => [styles.card, pressed && { opacity: 0.7 }]}
    >
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
          <Pill label="Edit" grow disabled={busy} onPress={onEdit} />
          <Pill label="Reject" grow disabled={busy} onPress={() => onReview(d, "rejected")} />
        </View>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4, marginBottom: 20 },
  processing: { alignItems: "center", paddingVertical: 56 },
  photo: { width: "100%", borderRadius: 16, marginBottom: 16, backgroundColor: colors.surface },
  failure: {
    marginTop: 24,
    padding: 16,
    borderRadius: 18,
    backgroundColor: colors.dangerSurface,
  },
  section: { marginTop: 32 },
  chips: { flexDirection: "row", flexWrap: "wrap", columnGap: 8 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 20,
    padding: 16,
    marginBottom: 12,
    gap: 2,
  },
  actions: { flexDirection: "row", gap: 10, marginTop: 14 },
  add: { flexDirection: "row", marginTop: 20 },
  relatedRow: { paddingVertical: 10, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: colors.hairline },
  relatedTitle: { color: colors.text, fontSize: 16, marginTop: 2 },
  bottom: {
    position: "absolute",
    left: 20,
    right: 20,
    bottom: 0,
    flexDirection: "row",
  },
});
