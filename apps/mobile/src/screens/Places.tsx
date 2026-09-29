import { Ionicons } from "@expo/vector-icons";
import { type ComponentProps, type ReactNode, useEffect, useState } from "react";
import { BackHandler, Pressable, StyleSheet, View } from "react-native";
import Animated, {
  cancelAnimation,
  Easing,
  FadeIn,
  FadeOut,
  useAnimatedStyle,
  useReducedMotion,
  useSharedValue,
  withRepeat,
  withSpring,
  withTiming,
} from "react-native-reanimated";
import { haptic } from "../haptics";
import { makeStyles, useTheme } from "../theme";

type IconName = ComponentProps<typeof Ionicons>["name"];
// `fresh`: something was added there since you last looked (src/news).
export type Place = { key: string; icon: IconName; label: string; onPress: () => void; fresh?: boolean };

const SIZE = 48;
const GAP = 4;

// Where memo's other screens are (Planner, People, Review, Faith, Habits): one circle
// above the record button that opens upward into a column of icons. Home's header
// used to hold them as a row of circles; they are one tap further away now, and
// by the thumb, next to the other things you do from Home.
//
// When a place has something new - a todo taken from a note, a prayer, a day counted -
// the closed circle breathes an amber ring, and once open, that place's icon pulses
// and carries a dot. With Reduce Motion on, only the dots.
export const PLACES_SIZE = SIZE;

export function Places({ places, bottom, right }: { places: Place[]; bottom: number; right: number }) {
  const styles = useStyles();
  const { colors } = useTheme();
  const [open, setOpen] = useState(false);
  const height = useSharedValue(SIZE);

  useEffect(() => {
    height.value = withSpring(open ? SIZE + places.length * (SIZE + GAP) : SIZE, { damping: 18, stiffness: 220 });
  }, [open, places.length, height]);
  const capsule = useAnimatedStyle(() => ({ height: height.value }));

  const reduced = useReducedMotion();
  const anyFresh = places.some((p) => p.fresh);
  const breath = useSharedValue(0);
  useEffect(() => {
    if (anyFresh && !reduced) {
      breath.value = 0;
      breath.value = withRepeat(withTiming(1, { duration: 1600, easing: Easing.out(Easing.quad) }), -1, false);
    } else {
      cancelAnimation(breath);
      breath.value = 0;
    }
  }, [anyFresh, reduced, breath]);
  const ring = useAnimatedStyle(() => ({
    opacity: 0.7 * (1 - breath.value),
    transform: [{ scale: 1 + 0.5 * breath.value }],
  }));

  useEffect(() => {
    if (!open) return;
    const sub = BackHandler.addEventListener("hardwareBackPress", () => {
      setOpen(false);
      return true;
    });
    return () => sub.remove();
  }, [open]);

  const toggle = () => {
    haptic.tap();
    setOpen((o) => !o);
  };
  const go = (place: Place) => {
    haptic.pick();
    setOpen(false);
    place.onPress();
  };

  return (
    <>
      {open && (
        <Animated.View entering={FadeIn.duration(160)} exiting={FadeOut.duration(140)} style={[StyleSheet.absoluteFill, styles.scrim]}>
          <Pressable style={{ flex: 1 }} onPress={() => setOpen(false)} accessibilityLabel="Close" />
        </Animated.View>
      )}
      {anyFresh && !open && !reduced && (
        <Animated.View pointerEvents="none" style={[styles.ring, { right, bottom, borderColor: colors.record }, ring]} />
      )}
      <Animated.View style={[styles.capsule, { right, bottom }, capsule]}>
        <Pressable
          onPress={toggle}
          accessibilityRole="button"
          accessibilityLabel={open ? "Close" : "Go to Planner, People and more"}
          accessibilityState={{ expanded: open }}
          style={({ pressed }) => [styles.slot, pressed && styles.pressed]}
        >
          <Ionicons name={open ? "close" : "compass-outline"} size={22} color={colors.text} />
          {anyFresh && !open && <View style={[styles.dot, { backgroundColor: colors.record }]} />}
        </Pressable>
        {open &&
          places.map((place, i) => (
            <Animated.View key={place.key} entering={FadeIn.duration(160).delay(40 + i * 30)}>
              <Pressable
                onPress={() => go(place)}
                accessibilityRole="button"
                accessibilityLabel={place.fresh ? `${place.label}, something new` : place.label}
                style={({ pressed }) => [styles.slot, { marginBottom: GAP }, pressed && styles.pressed]}
              >
                <Pulse on={!!place.fresh && !reduced}>
                  <Ionicons name={place.icon} size={21} color={place.fresh ? colors.record : colors.text} />
                </Pulse>
                {place.fresh && <View style={[styles.dot, { backgroundColor: colors.record }]} />}
              </Pressable>
            </Animated.View>
          ))}
      </Animated.View>
    </>
  );
}

// A gentle, endless swell for an icon with something new behind it.
function Pulse({ on, children }: { on: boolean; children: ReactNode }) {
  const t = useSharedValue(0);
  useEffect(() => {
    if (on) t.value = withRepeat(withTiming(1, { duration: 700, easing: Easing.inOut(Easing.quad) }), -1, true);
    else {
      cancelAnimation(t);
      t.value = 0;
    }
  }, [on, t]);
  const style = useAnimatedStyle(() => ({ transform: [{ scale: 1 + 0.14 * t.value }] }));
  return <Animated.View style={style}>{children}</Animated.View>;
}

const useStyles = makeStyles((colors) => ({
  scrim: { backgroundColor: "rgba(0,0,0,0.35)" },
  capsule: {
    position: "absolute",
    width: SIZE,
    borderRadius: SIZE / 2,
    backgroundColor: colors.surface,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.hairline,
    overflow: "hidden",
    flexDirection: "column-reverse",
  },
  slot: { width: SIZE, height: SIZE, alignItems: "center", justifyContent: "center" },
  ring: { position: "absolute", width: SIZE, height: SIZE, borderRadius: SIZE / 2, borderWidth: 2 },
  dot: { position: "absolute", top: 9, right: 9, width: 8, height: 8, borderRadius: 4 },
  pressed: { opacity: 0.55 },
}));

