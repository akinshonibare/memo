import { Ionicons } from "@expo/vector-icons";
import { useRef, useState } from "react";
import {
  type NativeScrollEvent,
  type NativeSyntheticEvent,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  useWindowDimensions,
  View,
} from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, Me } from "../api";
import { Pill } from "../components";
import { haptic } from "../haptics";
import { useKeyboardHeight } from "../keyboard";
import { colors, fonts, ui } from "../ui";

type Page = { icon: keyof typeof Ionicons.glyphMap; title: string; body: string };

// Three things worth knowing, then the one question that makes the app personal.
// Short on purpose: people skip long onboarding, and everything here is discoverable.
const PAGES: Page[] = [
  {
    icon: "mic-outline",
    title: "Capture it any way",
    body: "Type a note, talk, or snap a photo. Half a thought is fine - memo keeps the whole of it.",
  },
  {
    icon: "calendar-outline",
    title: "memo does the sorting",
    body: "Todos, events and ideas are pulled out for you and land in your Planner. You confirm what's right.",
  },
  {
    icon: "chatbubble-ellipses-outline",
    title: "Ask it anything",
    body: "Type or talk. Answers come from your own notes, with the notes they came from.",
  },
];

export function OnboardingScreen({
  api,
  me,
  onDone,
}: {
  api: Api;
  me: Me | null;
  onDone: (me: Me | null) => void;
}) {
  const { width } = useWindowDimensions();
  const insets = useSafeAreaInsets();
  const keyboard = useKeyboardHeight();
  const [page, setPage] = useState(0);
  const [name, setName] = useState(me?.name ?? "");
  const [busy, setBusy] = useState(false);
  const scroller = useRef<ScrollView>(null);
  const last = PAGES.length; // the name page

  const onScroll = (e: NativeSyntheticEvent<NativeScrollEvent>) => {
    const next = Math.round(e.nativeEvent.contentOffset.x / width);
    if (next !== page) {
      haptic.pick();
      setPage(next);
    }
  };

  const go = (to: number) => scroller.current?.scrollTo({ x: to * width, animated: true });

  const finish = async () => {
    setBusy(true);
    let updated: Me | null = me;
    const chosen = name.trim();
    if (chosen && chosen !== (me?.name ?? "")) {
      try {
        updated = await api.updateProfile({ name: chosen });
      } catch {
        // The name can be set later in Profile; never trap someone in onboarding.
      }
    }
    haptic.done();
    onDone(updated);
  };

  return (
    <View style={[ui.screen, { paddingBottom: Math.max(insets.bottom, 16) + keyboard }]}>
      <ScrollView
        ref={scroller}
        horizontal
        pagingEnabled
        showsHorizontalScrollIndicator={false}
        onMomentumScrollEnd={onScroll}
        keyboardShouldPersistTaps="handled"
      >
        {PAGES.map((p) => (
          <View key={p.title} style={[styles.page, { width }]}>
            <View style={styles.badge}>
              <Ionicons name={p.icon} size={34} color={colors.record} />
            </View>
            <Text style={styles.title}>{p.title}</Text>
            <Text style={styles.body}>{p.body}</Text>
          </View>
        ))}
        <View style={[styles.page, { width }]}>
          <Text style={styles.title}>What should memo call you?</Text>
          <Text style={styles.body}>It uses your name when it answers. You can change it any time in Profile.</Text>
          <TextInput
            style={[ui.input, styles.input]}
            value={name}
            onChangeText={setName}
            placeholder="Your first name"
            placeholderTextColor={colors.muted}
            autoCapitalize="words"
            returnKeyType="done"
            onSubmitEditing={finish}
            maxLength={60}
          />
        </View>
      </ScrollView>

      <View style={styles.dots}>
        {[...PAGES, null].map((_, i) => (
          <View key={i} style={[styles.dot, i === page && styles.dotOn]} />
        ))}
      </View>
      <View style={styles.actions}>
        {page < last ? (
          <>
            <Pill label="Skip" onPress={() => go(last)} />
            <View style={{ flex: 1 }} />
            <Pill label="Next" tone="light" onPress={() => go(page + 1)} />
          </>
        ) : (
          <Pill label={name.trim() ? "Start" : "Skip for now"} tone="light" grow onPress={finish} busy={busy} />
        )}
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, justifyContent: "center", paddingHorizontal: 32, gap: 14 },
  badge: {
    width: 72,
    height: 72,
    borderRadius: 36,
    backgroundColor: colors.surface,
    alignItems: "center",
    justifyContent: "center",
    marginBottom: 12,
  },
  title: { fontFamily: fonts.serif, fontSize: 38, lineHeight: 42, color: colors.text },
  body: { color: colors.muted, fontSize: 17, lineHeight: 25 },
  input: { marginTop: 12 },
  dots: { flexDirection: "row", justifyContent: "center", gap: 8, marginBottom: 20 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: colors.hairline },
  dotOn: { backgroundColor: colors.record, width: 20 },
  actions: { flexDirection: "row", alignItems: "center", paddingHorizontal: 24 },
});
