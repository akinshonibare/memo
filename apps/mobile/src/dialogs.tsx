import { Ionicons } from "@expo/vector-icons";
import { type ComponentProps, useEffect, useState, useSyncExternalStore } from "react";
import { type KeyboardTypeOptions, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import Animated, { FadeIn, SlideInDown } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { haptic } from "./haptics";
import { useKeyboardHeight } from "./keyboard";
import { PressableScale } from "./motion";
import { makeStyles, useTheme } from "./theme";

// memo's own menus and dialogs, in place of the system action sheet and alert: the
// same questions, drawn in memo's colours and type, with an icon on each choice.
//
// Called like the system ones - `await choose(...)`, `await confirm(...)`,
// `await ask(...)` - and answered by a <Dialogs /> host. Like the lock cover, a host
// is drawn inside every sheet as well as over the app: iOS won't present anything
// over a sheet that's already showing, so a dialog has to live inside the sheet it
// belongs to. Only the most recently mounted host draws, so a dialog asked for from
// an open sheet appears in that sheet, once.

type IconName = ComponentProps<typeof Ionicons>["name"];

export type Choice = { label: string; icon?: IconName; destructive?: boolean };

type Choose = {
  kind: "choose";
  title?: string;
  message?: string;
  options: Choice[];
  cancel: string;
  resolve: (index: number | null) => void;
};
type Ask = {
  kind: "ask";
  title: string;
  message?: string;
  initial?: string;
  placeholder?: string;
  action: string;
  cancel: string;
  keyboardType?: KeyboardTypeOptions;
  allowEmpty?: boolean;
  resolve: (value: string | null) => void;
};
type Tell = { kind: "tell"; title: string; message?: string; ok: string; resolve: () => void };
type Request = (Choose | Ask | Tell) & { id?: number };

// --- the store ------------------------------------------------------------------------

let queue: Request[] = [];
let nextRequest = 1;
let hosts: number[] = [];
let nextHost = 1;
let version = 0;
const listeners = new Set<() => void>();

function changed() {
  version += 1;
  for (const l of listeners) l();
}

function push(request: Request) {
  request.id = nextRequest++;
  queue = [...queue, request];
  changed();
}

function settle(request: Request) {
  queue = queue.filter((r) => r !== request);
  changed();
}

const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

// --- asking ---------------------------------------------------------------------------

// A menu of choices: the index picked, or null for Cancel (or a tap outside).
export function choose(o: { title?: string; message?: string; options: Choice[]; cancel?: string }): Promise<number | null> {
  return new Promise((resolve) => push({ kind: "choose", cancel: "Cancel", ...o, resolve }));
}

// A yes-or-no with the yes spelled out ("Delete", "Stop tracking"). Destructive by
// default, because that's what memo asks about.
export async function confirm(o: {
  title: string;
  message?: string;
  action: string;
  icon?: IconName;
  destructive?: boolean;
  cancel?: string;
}): Promise<boolean> {
  haptic.warn();
  const picked = await new Promise<number | null>((resolve) =>
    push({
      kind: "choose",
      title: o.title,
      message: o.message,
      options: [{ label: o.action, icon: o.icon, destructive: o.destructive ?? true }],
      cancel: o.cancel ?? "Cancel",
      resolve,
    }),
  );
  return picked === 0;
}

// A line of text: the trimmed answer, or null if cancelled. Empty isn't an answer
// unless `allowEmpty` (clearing a name or a note), when it resolves "".
export function ask(o: {
  title: string;
  message?: string;
  initial?: string;
  placeholder?: string;
  action?: string;
  cancel?: string;
  keyboardType?: KeyboardTypeOptions;
  allowEmpty?: boolean;
}): Promise<string | null> {
  return new Promise((resolve) => push({ kind: "ask", action: "Save", cancel: "Cancel", ...o, resolve }));
}

// Something to know, with one button.
export function tell(title: string, message?: string, ok = "OK"): Promise<void> {
  return new Promise((resolve) => push({ kind: "tell", title, message, ok, resolve }));
}

// --- the host -------------------------------------------------------------------------

export function Dialogs() {
  const [id] = useState(() => nextHost++);
  useEffect(() => {
    hosts = [...hosts, id];
    changed();
    return () => {
      hosts = hosts.filter((h) => h !== id);
      changed();
    };
  }, [id]);
  useSyncExternalStore(subscribe, () => version);
  const request = queue[0];
  if (!request || hosts[hosts.length - 1] !== id) return null;
  return <Dialog key={request.id} request={request} />;
}

function Dialog({ request }: { request: Request }) {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const keyboard = useKeyboardHeight();

  const dismiss = () => {
    settle(request);
    if (request.kind === "choose") request.resolve(null);
    else if (request.kind === "ask") request.resolve(null);
    else request.resolve();
  };

  return (
    <View style={StyleSheet.absoluteFill} accessibilityViewIsModal onAccessibilityEscape={dismiss}>
      <Animated.View entering={FadeIn.duration(160)} style={StyleSheet.absoluteFill}>
        <Pressable
          style={styles.scrim}
          onPress={dismiss}
          accessibilityRole="button"
          accessibilityLabel="Close"
        />
      </Animated.View>
      <Animated.View
        entering={SlideInDown.duration(240)}
        style={[styles.anchor, { paddingBottom: Math.max(insets.bottom, 12) + (keyboard ? keyboard - insets.bottom : 0) }]}
        pointerEvents="box-none"
      >
        {request.kind === "choose" ? (
          <ChooseCard request={request} />
        ) : request.kind === "ask" ? (
          <AskCard request={request} />
        ) : (
          <TellCard request={request} />
        )}
      </Animated.View>
    </View>
  );
}

function Heading({ title, message }: { title?: string; message?: string }) {
  const styles = useStyles();
  if (!title && !message) return null;
  return (
    <View style={styles.heading}>
      {title ? (
        <Text style={styles.title} accessibilityRole="header">
          {title}
        </Text>
      ) : null}
      {message ? <Text style={styles.message}>{message}</Text> : null}
    </View>
  );
}

function ChooseCard({ request }: { request: Choose }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const pick = (index: number | null) => {
    if (index !== null) haptic.tap();
    settle(request);
    request.resolve(index);
  };
  return (
    <>
      <View style={styles.card}>
        <Heading title={request.title} message={request.message} />
        {request.options.map((o, i) => {
          const color = o.destructive ? colors.danger : colors.text;
          return (
            <Pressable
              key={o.label}
              onPress={() => pick(i)}
              accessibilityRole="button"
              style={({ pressed }) => [
                styles.row,
                (i > 0 || request.title || request.message) && styles.rowRule,
                pressed && styles.pressed,
              ]}
            >
              {o.icon ? (
                <View style={[styles.rowIcon, o.destructive && { backgroundColor: colors.dangerSurface }]}>
                  <Ionicons name={o.icon} size={18} color={color} />
                </View>
              ) : null}
              <Text style={[styles.rowLabel, { color }]}>{o.label}</Text>
            </Pressable>
          );
        })}
      </View>
      <PressableScale onPress={() => pick(null)} accessibilityRole="button" style={styles.cancel} pressedStyle={styles.pressed}>
        <Text style={styles.cancelText}>{request.cancel}</Text>
      </PressableScale>
    </>
  );
}

function AskCard({ request }: { request: Ask }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const [value, setValue] = useState(request.initial ?? "");
  const answer = value.trim();
  const ready = !!answer || !!request.allowEmpty;
  const done = (result: string | null) => {
    settle(request);
    request.resolve(result);
  };
  return (
    <View style={styles.card}>
      <Heading title={request.title} message={request.message} />
      <TextInput
        value={value}
        onChangeText={setValue}
        placeholder={request.placeholder}
        placeholderTextColor={colors.faint}
        autoFocus
        selectTextOnFocus
        keyboardType={request.keyboardType}
        returnKeyType="done"
        onSubmitEditing={() => ready && done(answer)}
        style={styles.input}
        accessibilityLabel={request.title}
      />
      <View style={styles.buttons}>
        <PressableScale onPress={() => done(null)} accessibilityRole="button" style={styles.button} pressedStyle={styles.pressed}>
          <Text style={styles.cancelText}>{request.cancel}</Text>
        </PressableScale>
        <PressableScale
          onPress={() => ready && done(answer)}
          disabled={!ready}
          accessibilityRole="button"
          accessibilityState={{ disabled: !ready }}
          style={[styles.button, styles.primary, !ready && styles.pressed]}
          pressedStyle={styles.pressed}
        >
          <Text style={[styles.cancelText, { color: colors.bg }]}>{request.action}</Text>
        </PressableScale>
      </View>
    </View>
  );
}

function TellCard({ request }: { request: Tell }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const ok = () => {
    settle(request);
    request.resolve();
  };
  return (
    <View style={styles.card}>
      <Heading title={request.title} message={request.message} />
      <View style={styles.buttons}>
        <PressableScale onPress={ok} accessibilityRole="button" style={[styles.button, styles.primary]} pressedStyle={styles.pressed}>
          <Text style={[styles.cancelText, { color: colors.bg }]}>{request.ok}</Text>
        </PressableScale>
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  scrim: { flex: 1, backgroundColor: "rgba(0,0,0,0.45)" },
  anchor: { position: "absolute", left: 0, right: 0, bottom: 0, paddingHorizontal: 12, gap: 8 },
  card: { backgroundColor: colors.surface, borderRadius: 24, overflow: "hidden" },
  heading: { paddingHorizontal: 20, paddingTop: 18, paddingBottom: 14, gap: 4 },
  title: { color: colors.text, fontSize: 17, fontWeight: "600" },
  message: { color: colors.muted, fontSize: 15, lineHeight: 21 },
  row: { flexDirection: "row", alignItems: "center", gap: 12, paddingHorizontal: 20, minHeight: 56 },
  rowRule: { borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: colors.hairline },
  rowIcon: {
    width: 32,
    height: 32,
    borderRadius: 16,
    backgroundColor: colors.surfaceAlt,
    alignItems: "center",
    justifyContent: "center",
  },
  rowLabel: { fontSize: 17 },
  pressed: { opacity: 0.6 },
  cancel: {
    backgroundColor: colors.surface,
    borderRadius: 24,
    minHeight: 56,
    alignItems: "center",
    justifyContent: "center",
  },
  cancelText: { color: colors.text, fontSize: 17, fontWeight: "600" },
  input: {
    marginHorizontal: 20,
    backgroundColor: colors.surfaceAlt,
    borderRadius: 14,
    paddingHorizontal: 14,
    paddingVertical: 12,
    color: colors.text,
    fontSize: 17,
  },
  buttons: { flexDirection: "row", gap: 10, padding: 16 },
  button: {
    flex: 1,
    minHeight: 50,
    borderRadius: 25,
    backgroundColor: colors.surfaceAlt,
    alignItems: "center",
    justifyContent: "center",
  },
  primary: { backgroundColor: colors.text },
}));
