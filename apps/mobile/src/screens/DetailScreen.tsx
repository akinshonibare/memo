import { useCallback, useEffect, useRef, useState } from "react";
import {
  Image,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Api, type Capture, type CaptureDetail, type Derivation, type Word } from "../api";
import { asTranslation, openInBible } from "../bible";
import { AskBar, IconCircle, Pill, confirmDelete } from "../components";
import { ACTIVE_STATUSES, formatClock, formatDue, formatStamp, formatWhen, headline, statusLabel } from "../format";
import { useRefetchOnFocus } from "../focus";
import { InkText, LiveDot } from "../motion";
import { playUrl, seekPlayback, stopPlayback, usePlaybackDerived, usePlayingId } from "../playback";
import { useUi } from "../ui";
import { makeStyles, useTheme } from "../theme";
import { choose } from "../dialogs";
import { DerivationSheet } from "./DerivationSheet";
import { MentionCard } from "./PeopleParts";
import { haptic } from "../haptics";
import { usePullToRefresh } from "../refresh";

const KINDS: Derivation["kind"][] = ["todo", "event", "idea"];

// Pick what to add, in memo's own menu.
async function chooseKind(onPick: (kind: Derivation["kind"]) => void) {
  const picked = await choose({
    title: "What did it miss?",
    options: [
      { label: "A todo", icon: "checkbox-outline" },
      { label: "An event", icon: "calendar-outline" },
      { label: "An idea", icon: "bulb-outline" },
    ],
  });
  if (picked !== null) onPick(KINDS[picked]);
}

const POLL_MS = 3000;

// Extraction is never silent in either direction (issue #44): the processing state is
// visible, every derivation is one tap to confirm or reject, and a failure shows its
// reason with a retry.
export function DetailScreen({
  api,
  id,
  refreshKey,
  onBack,
  onAsk,
  onChanged,
  onOpen,
  onOpenPerson,
  faith = false,
  translation,
}: {
  api: Api;
  id: string;
  refreshKey: number;
  onBack: () => void;
  onAsk: () => void;
  onChanged: () => void;
  onOpen: (id: string) => void;
  onOpenPerson: (id: string) => void;
  // Faith (ADR 0042): whether it's on (a note can then move in or out), and the
  // translation a passage opens in.
  faith?: boolean;
  translation?: string;
}) {
  const ui = useUi();
  const styles = useStyles();
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

  // The words ink in (ADR 0032) when they arrive while you're looking: a voice note or
  // photo opened while it was still being read.
  const lastText = useRef<string | null>(null);
  const [inked, setInked] = useState(false);
  const load = useCallback(async () => {
    try {
      const next = await api.getCapture(id);
      const had = lastText.current;
      const has = (next.text ?? "").trim();
      if (had !== null && !had && has) setInked(true);
      lastText.current = has;
      setCapture(next);
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, id]);
  const pull = usePullToRefresh(load);

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
  }, [load, active, refreshKey]);
  useRefetchOnFocus(load);

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
  // Into Faith, or back out (ADR 0042): the server moves its chunks with it and finds
  // its passages again. Back to wherever it now lives is the next screen's job.
  const move = async (space: "main" | "faith") => {
    if (!capture) return;
    setBusy("move");
    try {
      await api.moveCapture(capture.id, space);
      haptic.done();
      onChanged();
      await load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  };

  const remove = () =>
    confirmDelete("Delete this note and everything memo took from it?", async () => {
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
        {capture && <IconCircle name="trash-outline" onPress={remove} accessibilityLabel="Delete note" />}
      </View>
      <ScrollView
        contentContainerStyle={[ui.pad, { paddingBottom: 140 + insets.bottom }]}
        keyboardShouldPersistTaps="handled"
        refreshControl={pull}
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
            {playingId === capture.id && capture.words && capture.words.length > 0 ? (
              <Karaoke id={capture.id} words={capture.words} />
            ) : (
              <>
                <InkText
                  key={inked ? "ink" : "plain"}
                  text={headline(capture).title}
                  play={inked}
                  once={`${capture.id}:title`}
                  style={[ui.h2, { marginTop: 6 }]}
                />
                {headline(capture).rest && (
                  <InkText
                    key={inked ? "ink-rest" : "plain-rest"}
                    text={headline(capture).rest!}
                    play={inked}
                    once={`${capture.id}:rest`}
                    style={[ui.body, { marginTop: 14 }]}
                  />
                )}
              </>
            )}
          </>
        )}
        {error && <Text style={[ui.danger, { marginTop: 16 }]}>{error}</Text>}

        {capture && ACTIVE_STATUSES.has(capture.status) && (
          <View style={styles.processing} accessible accessibilityLabel={statusLabel(capture.status) ?? undefined}>
            <LiveDot live size={14} />
            <Text style={[ui.meta, { marginTop: 14 }]}>{statusLabel(capture.status)}</Text>
          </View>
        )}

        {capture?.status === "failed" && (
          <View style={styles.failure}>
            <Text style={ui.danger}>{"Couldn't process this note."}</Text>
            <Text style={[ui.bodyMuted, { marginTop: 4 }]}>{capture.failure_reason}</Text>
          </View>
        )}

        {(capture?.passages?.length ?? 0) > 0 && (
          <View style={styles.section}>
            <Text style={ui.label}>Passages</Text>
            <View style={styles.passages}>
              {capture!.passages!.map((p) => (
                <Pill
                  key={p.ref}
                  label={p.ref}
                  icon="book-outline"
                  onPress={() => openInBible(p, asTranslation(translation))}
                />
              ))}
            </View>
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
              onPress={() => void chooseKind((kind) => setEditing({ kind }))}
            />
          </View>
        )}
        {capture && faith && (
          <Pressable
            onPress={() => void move(capture.space === "faith" ? "main" : "faith")}
            disabled={busy === "move"}
            style={({ pressed }) => [styles.move, pressed && { opacity: 0.6 }]}
            accessibilityRole="button"
          >
            <Text style={ui.meta}>
              {capture.space === "faith" ? "Move to your notes" : "Move to Within"}
            </Text>
          </Pressable>
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

// The transcript following the recording (ADR 0039): what's been said in full colour,
// the word being said in amber, what's to come quieter. Tap a word to jump there.
function Karaoke({ id, words }: { id: string; words: Word[] }) {
  const ui = useUi();
  const { colors } = useTheme();
  const wordAt = useCallback(
    (ms: number) => {
      let at = -1;
      for (let i = 0; i < words.length && words[i][0] <= ms; i++) at = i;
      return at;
    },
    [words],
  );
  const current = usePlaybackDerived(id, wordAt);
  return (
    <Text style={[ui.body, { marginTop: 10, fontSize: 19, lineHeight: 28 }]} accessibilityLabel={words.map((w) => w[2]).join(" ")}>
      {words.map(([start, , word], i) => (
        <Text
          key={i}
          onPress={() => seekPlayback(start)}
          style={{
            color: i === current ? colors.record : i < current ? colors.text : colors.faint,
            fontWeight: i === current ? "600" : "400",
          }}
        >
          {word}
          {i < words.length - 1 ? " " : ""}
        </Text>
      ))}
    </Text>
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
  const ui = useUi();
  const styles = useStyles();
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

const useStyles = makeStyles((colors) => ({
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
  passages: { flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 8 },
  move: { alignSelf: "center", marginTop: 28, padding: 8 },
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
}));
