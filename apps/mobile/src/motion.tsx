import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import {
  Dimensions,
  type GestureResponderEvent,
  Pressable,
  type PressableProps,
  type StyleProp,
  StyleSheet,
  Text,
  type TextStyle,
  View,
  type ViewStyle,
} from "react-native";
import Animated, {
  cancelAnimation,
  Easing,
  FadeInDown,
  FadeOut,
  LinearTransition,
  useAnimatedStyle,
  useFrameCallback,
  useReducedMotion,
  useSharedValue,
  withDelay,
  withRepeat,
  withSequence,
  withSpring,
  withTiming,
  type SharedValue,
} from "react-native-reanimated";
import { type Particle, seasonParticle } from "./delight";
import { haptic } from "./haptics";
import { useTheme } from "./theme";

// Motion (docs/brand.md, ADR 0032). Each movement means one thing, paired with the
// haptic vocabulary in haptics.ts: a tap squeezes, something starting swells, something
// done lands and settles. One signature animation per screen; springs, not durations;
// and with Reduce Motion on, every one of these is still or a plain fade.

// Reanimated 4's spring defaults are heavy (mass 4), so every spring here names its
// mass: a press answers in about a tenth of a second, a landing overshoots once.
export const springs = {
  press: { damping: 18, stiffness: 420, mass: 0.6 },
  release: { damping: 14, stiffness: 260, mass: 0.6 },
  settle: { damping: 12, stiffness: 180, mass: 1 },
  voice: { damping: 16, stiffness: 140, mass: 1 },
};

// Lists: rows glide into place when others come or go, and a new one drops in.
export const listLayout = LinearTransition.springify().damping(18).stiffness(160).mass(1);
export const rowEnter = FadeInDown.springify().damping(16).stiffness(170).mass(1);
export const rowExit = FadeOut.duration(180);

const AnimatedPressable = Animated.createAnimatedComponent(Pressable);

// A Pressable that squeezes while held. `style` is static (a press is shown by the
// scale, and by `pressedStyle` if given), so it can sit on the UI thread.
export function PressableScale({
  scaleTo = 0.95,
  style,
  pressedStyle,
  onPressIn,
  onPressOut,
  children,
  ...rest
}: Omit<PressableProps, "style" | "children"> & {
  scaleTo?: number;
  style?: StyleProp<ViewStyle>;
  pressedStyle?: StyleProp<ViewStyle>;
  children?: ReactNode;
}) {
  const scale = useSharedValue(1);
  const [pressed, setPressed] = useState(false);
  const animated = useAnimatedStyle(() => ({ transform: [{ scale: scale.value }] }));
  return (
    <AnimatedPressable
      {...rest}
      onPressIn={(e: GestureResponderEvent) => {
        scale.set(withSpring(scaleTo, springs.press));
        if (pressedStyle) setPressed(true);
        onPressIn?.(e);
      }}
      onPressOut={(e: GestureResponderEvent) => {
        scale.set(withSpring(1, springs.release));
        if (pressedStyle) setPressed(false);
        onPressOut?.(e);
      }}
      style={[style, pressed && pressedStyle, animated]}
    >
      {children}
    </AnimatedPressable>
  );
}

// The amber full stop in the wordmark is memo's status light (ADR 0032): still when
// nothing is happening, breathing while a note is being processed or waiting to sync.
// `glow` rings it with a halo for a moment worth marking (ADR 0041); `hop` is a counter,
// and each increase bounces it once.
export function LiveDot({
  live,
  size = 7,
  style,
  glow = false,
  hop = 0,
}: {
  live: boolean;
  size?: number;
  style?: StyleProp<ViewStyle>;
  glow?: boolean;
  hop?: number;
}) {
  const { colors } = useTheme();
  const reduced = useReducedMotion();
  const breath = useSharedValue(0);
  const bounce = useSharedValue(1);
  const halo = useSharedValue(0);
  useEffect(() => {
    if (live && !reduced) {
      breath.set(
        withRepeat(
          withSequence(
            withTiming(1, { duration: 900, easing: Easing.inOut(Easing.sin) }),
            withTiming(0, { duration: 900, easing: Easing.inOut(Easing.sin) }),
          ),
          -1,
        ),
      );
    } else {
      cancelAnimation(breath);
      breath.set(withTiming(0, { duration: 300 }));
    }
  }, [live, reduced, breath]);
  useEffect(() => {
    if (hop > 0) bounce.set(withSequence(withTiming(1.9, { duration: 90 }), withSpring(1, springs.settle)));
  }, [hop, bounce]);
  useEffect(() => {
    if (glow && !reduced) {
      halo.set(withRepeat(withTiming(1, { duration: 1400, easing: Easing.out(Easing.quad) }), -1));
    } else {
      cancelAnimation(halo);
      halo.set(withTiming(0, { duration: 300 }));
    }
  }, [glow, reduced, halo]);
  const animated = useAnimatedStyle(() => ({
    transform: [{ scale: (1 + breath.value * 0.7) * bounce.value }],
    opacity: 1 - breath.value * 0.45,
  }));
  const ring = useAnimatedStyle(() => ({
    opacity: halo.value > 0 ? 0.7 * (1 - halo.value) : 0,
    transform: [{ scale: 1 + halo.value * 3 }],
  }));
  const disc = { width: size, height: size, borderRadius: size / 2, backgroundColor: colors.record };
  return (
    <View style={style} accessibilityElementsHidden importantForAccessibility="no">
      <Animated.View style={[disc, { position: "absolute" }, ring]} />
      <Animated.View style={[disc, animated]} />
    </View>
  );
}

// "memo." with the living dot. The dot sits on the serif's baseline. With `playful`
// (Home only), tapping the dot hops it; five taps within three seconds let the season
// out (ADR 0041): a burst from the dot at once, then a swarm across the screen.
const SWARM_TAPS = 5;
const SWARM_WINDOW_MS = 3000;
export function Wordmark({
  live,
  size = 38,
  style,
  glow,
  playful,
}: {
  live: boolean;
  size?: number;
  style?: StyleProp<ViewStyle>;
  glow?: boolean;
  playful?: boolean;
}) {
  const { colors } = useTheme();
  const dotSize = Math.max(5, Math.round(size * 0.18));
  const [hops, setHops] = useState(0);
  const taps = useRef<number[]>([]);
  const dotRef = useRef<View>(null);
  const tap = () => {
    haptic.tap();
    setHops((h) => h + 1);
    const now = Date.now();
    taps.current = [...taps.current.filter((t) => now - t < SWARM_WINDOW_MS), now];
    if (taps.current.length >= SWARM_TAPS) {
      taps.current = [];
      setTimeout(haptic.done, 120);
      dotRef.current?.measureInWindow((x, y, w, h) => {
        celebrate(x + w / 2, y + h / 2);
        celebrate(x + w / 2, y + h / 2, true);
      });
    }
  };
  const dot = (
    <View ref={dotRef} collapsable={false}>
      <LiveDot live={live} size={dotSize} glow={glow} hop={hops} />
    </View>
  );
  return (
    <View
      style={[{ flexDirection: "row", alignItems: "flex-end" }, style]}
      accessible
      accessibilityRole="header"
      accessibilityLabel="memo"
    >
      <Text style={{ fontFamily: "InstrumentSerif_400Regular", fontSize: size, lineHeight: size * 1.16, color: colors.text }}>
        memo
      </Text>
      <View style={{ marginLeft: size * 0.04, marginBottom: size * 0.27 }}>
        {playful ? (
          <Pressable onPress={tap} hitSlop={16} accessible={false}>
            {dot}
          </Pressable>
        ) : (
          dot
        )}
      </View>
    </View>
  );
}

// Words writing themselves in, once, when a note's text first arrives (ADR 0032).
// While it plays the words are laid out one by one; afterwards it is an ordinary Text,
// so truncation and selection behave as they always did.
const INK_WORDS = 24;
const INK_STEP = 55;
const INK_FADE = 380;
// What has inked in already, by `once`: text remounted later (after the transcript has
// followed a recording, say) appears as it is rather than writing itself in again.
const inkedAlready = new Set<string>();

export function InkText({
  text,
  play,
  once,
  style,
  numberOfLines,
}: {
  text: string;
  play: boolean;
  once: string;
  style?: StyleProp<TextStyle>;
  numberOfLines?: number;
}) {
  const reduced = useReducedMotion();
  const [inking, setInking] = useState(() => play && !reduced && !inkedAlready.has(once));
  useEffect(() => {
    if (!inking) return;
    inkedAlready.add(once);
    const words = Math.min(text.split(/\s+/).length, INK_WORDS);
    const timer = setTimeout(() => setInking(false), words * INK_STEP + INK_FADE + 60);
    return () => clearTimeout(timer);
  }, [inking, text, once]);

  if (!inking) {
    return (
      <Text style={style} numberOfLines={numberOfLines}>
        {text}
      </Text>
    );
  }
  const words = text.split(/\s+/).filter(Boolean);
  const head = words.slice(0, INK_WORDS);
  const tail = words.slice(INK_WORDS).join(" ");
  return (
    <View style={styles.inkRow} accessible accessibilityLabel={text}>
      {head.map((w, i) => (
        <Animated.Text
          key={`${i}-${w}`}
          style={style}
          entering={FadeInDown.duration(INK_FADE)
            .delay(i * INK_STEP)
            .withInitialValues({ opacity: 0, transform: [{ translateY: 5 }] })}
        >
          {w}{" "}
        </Animated.Text>
      ))}
      {tail ? (
        <Animated.Text style={style} entering={FadeInDown.duration(INK_FADE).delay(head.length * INK_STEP)}>
          {tail}
        </Animated.Text>
      ) : null}
    </View>
  );
}

// --- fireflies, and the season's other particles ---------------------------------------
// Ticking something off sends a few specks up from where you tapped; clearing the last
// thing on Today sends a swarm (ADR 0032). What rises is the season's (ADR 0041):
// fireflies in summer, leaves in autumn, snow in winter, petals in spring. Leaves and
// snow fall rather than rise, and a swarm of them comes down from the top of the screen.
// One host at the app root draws them; anything can ask for a burst.

type Fly = {
  id: number;
  dx: number;
  dy: number;
  hop: number; // an upward pop before a falling particle falls
  lead: number; // how far down the screen a falling swarm's particle starts
  glide: number; // 0..1: how much of a fall is steady rather than speeding up
  size: number;
  delay: number;
  duration: number;
  sway: number;
  spin: number;
  color: number; // index into the kind's colours
};
type Burst = { id: number; x: number; y: number; kind: Particle; flies: Fly[] };
let nextBurst = 1;
let emit: ((b: Burst) => void) | null = null;

const FALLS: Record<Particle, boolean> = { fireflies: false, petals: false, leaves: true, snow: true };

function flies(kind: Particle, big: boolean, height: number): Fly[] {
  const falls = FALLS[kind];
  const count = big ? (falls ? 34 : 26) : 10;
  return Array.from({ length: count }, (_, i) => ({
    id: i,
    dx: big && falls ? 0 : (Math.random() - 0.5) * (big ? 260 : 130) + (kind === "petals" ? 40 : 0),
    dy: falls
      ? big
        ? height * (0.75 + Math.random() * 0.3)
        : 90 + Math.random() * 90
      : -(big ? 180 + Math.random() * 360 : 70 + Math.random() * 120),
    hop: falls && !big ? 30 + Math.random() * 30 : 0,
    // A falling swarm starts on the screen and moves from the first frame: starting
    // above it and speeding up from rest hid the first second behind the status bar.
    lead: big && falls ? Math.random() * height * 0.15 : 0,
    glide: big && falls ? 0.45 : 0,
    size: (kind === "leaves" || kind === "petals" ? (big ? 8 : 6) : 3) + Math.random() * (big ? 5 : 3),
    delay: i * (big ? (falls ? 60 : 28) : 35),
    duration: (falls ? 1800 : 1100) + Math.random() * (falls && big ? 1600 : 900),
    sway: (falls ? 14 : 6) + Math.random() * 12,
    spin: (Math.random() - 0.5) * 720,
    color: Math.floor(Math.random() * 3),
  }));
}

export function celebrate(x: number, y: number, big = false, kind: Particle = seasonParticle()) {
  const { width, height } = Dimensions.get("window");
  // A falling swarm comes down across the whole width of the screen, from near the top.
  const fromTop = big && FALLS[kind];
  const burst: Burst = { id: nextBurst++, x: fromTop ? 0 : x, y: fromTop ? 0 : y, kind, flies: flies(kind, big, height) };
  if (fromTop) burst.flies.forEach((f) => (f.dx = Math.random() * width));
  emit?.(burst);
}

// From a press: where the finger was.
export function celebrateAt(e: GestureResponderEvent | undefined, big = false) {
  if (!e) return;
  celebrate(e.nativeEvent.pageX, e.nativeEvent.pageY, big);
}

export function FirefliesHost() {
  const reduced = useReducedMotion();
  const [bursts, setBursts] = useState<Burst[]>([]);
  const remove = useCallback((id: number) => setBursts((prev) => prev.filter((x) => x.id !== id)), []);
  useEffect(() => {
    emit = (b) => setBursts((prev) => [...prev.slice(-3), b]);
    return () => {
      emit = null;
    };
  }, []);
  if (reduced) return null;
  return (
    <View pointerEvents="none" style={StyleSheet.absoluteFill}>
      {bursts.map((b) => (
        <FireflyBurst key={b.id} burst={b} onDone={remove} />
      ))}
    </View>
  );
}

function FireflyBurst({ burst, onDone }: { burst: Burst; onDone: (id: number) => void }) {
  useEffect(() => {
    const longest = Math.max(...burst.flies.map((f) => f.delay + f.duration));
    const timer = setTimeout(() => onDone(burst.id), longest + 50);
    return () => clearTimeout(timer);
  }, [burst, onDone]);
  return (
    <>
      {burst.flies.map(({ id, ...f }) => (
        <Firefly key={id} x={burst.x} y={burst.y} kind={burst.kind} {...f} />
      ))}
    </>
  );
}

function particleColors(kind: Particle, accent: string, text: string): string[] {
  if (kind === "leaves") return [accent, "#C0572F", "#8C5A2B"];
  if (kind === "snow") return [text, text, "#DCE6F0"];
  if (kind === "petals") return ["#F4B6C2", "#F9D5DC", text];
  return [accent, accent, accent];
}

function Firefly({
  x,
  y,
  kind,
  dx,
  dy,
  hop,
  lead,
  glide,
  size,
  delay,
  duration,
  sway,
  spin,
  color,
}: Omit<Fly, "id"> & { x: number; y: number; kind: Particle }) {
  const { colors } = useTheme();
  const t = useSharedValue(0);
  const falls = FALLS[kind];
  useEffect(() => {
    t.set(withDelay(delay, withTiming(1, { duration, easing: falls ? Easing.linear : Easing.out(Easing.quad) })));
  }, [t, delay, duration, falls]);
  const animated = useAnimatedStyle(() => {
    const p = t.value;
    if (!falls) {
      // Up and out, with a lazy side-to-side drift and a flicker as it fades.
      const flicker = kind === "fireflies" ? 0.75 + 0.25 * Math.sin(p * 28) : 1;
      const opacity = p < 0.12 ? p / 0.12 : Math.max(0, 1 - (p - 0.12) / 0.88) * flicker;
      return {
        opacity,
        transform: [
          { translateX: dx * p + Math.sin(p * 9) * sway },
          { translateY: dy * p },
          { rotate: `${spin * p}deg` },
          { scale: 0.6 + 0.6 * Math.sin(Math.min(1, p * 1.4) * Math.PI) },
        ],
      };
    }
    // A pop up, then down with a swaying fall, fading only at the end.
    const opacity = p < 0.08 ? p / 0.08 : p > 0.8 ? Math.max(0, (1 - p) / 0.2) : 1;
    return {
      opacity,
      transform: [
        { translateX: dx + Math.sin(p * 7) * sway },
        { translateY: lead - hop * Math.sin(Math.min(1, p * 3) * Math.PI) + dy * (glide * p + (1 - glide) * p * p) },
        { rotate: `${spin * p}deg` },
      ],
    };
  });
  const flat = kind === "leaves" || kind === "petals";
  return (
    <Animated.View
      style={[
        {
          position: "absolute",
          left: x - size / 2,
          top: y - size / 2,
          width: flat ? size * 0.7 : size,
          height: flat ? size * 1.3 : size,
          borderRadius: flat ? size : size / 2,
          borderTopLeftRadius: kind === "leaves" ? 1 : undefined,
          borderBottomRightRadius: kind === "leaves" ? 1 : undefined,
          backgroundColor: particleColors(kind, colors.record, colors.text)[color],
        },
        animated,
      ]}
    />
  );
}

// A check that lands: the circle pops, overshoots, and settles (ADR 0032).
export function usePop(): [SharedValue<number>, () => void] {
  const scale = useSharedValue(1);
  const pop = () => {
    scale.set(withSequence(withTiming(0.7, { duration: 70 }), withSpring(1, springs.settle)));
  };
  return [scale, pop];
}

// --- voice -------------------------------------------------------------------------

// A dB reading from the recorder (iOS reports roughly -160 to 0) as 0..1, where speech
// at arm's length lands in the upper half.
export function levelFromDb(db: number | undefined): number {
  if (db == null || !Number.isFinite(db)) return 0;
  return Math.min(1, Math.max(0, (db + 55) / 45));
}

// The record orb (ADR 0032): an amber body that swells with your voice, with two
// softer halos drifting round it. `level` is 0..1; `state` dims it while paused, and
// `drop` shrinks it into a full stop falling toward the list when the note is saved.
export function VoiceOrb({
  level,
  state,
  drop,
  size = 180,
}: {
  level: number;
  state: "live" | "paused" | "saving";
  drop: boolean;
  size?: number;
}) {
  const { colors } = useTheme();
  const reduced = useReducedMotion();
  const lv = useSharedValue(0);
  const time = useSharedValue(0);
  const fall = useSharedValue(0);
  const dim = useSharedValue(1);

  useEffect(() => {
    lv.set(withSpring(state === "live" ? level : 0, springs.voice));
  }, [level, state, lv]);
  useEffect(() => {
    dim.set(withTiming(state === "paused" ? 0.45 : 1, { duration: 300 }));
  }, [state, dim]);
  useEffect(() => {
    if (drop) fall.set(withTiming(1, { duration: reduced ? 1 : 520, easing: Easing.in(Easing.cubic) }));
  }, [drop, fall, reduced]);

  useFrameCallback((frame) => {
    if (reduced) return;
    time.value += (frame.timeSincePreviousFrame ?? 16) / 1000;
  });

  const core = useAnimatedStyle(() => {
    const t = time.value;
    const l = lv.value;
    const wobble = (i: number) => size * (0.5 - 0.11 * l * (0.5 + 0.5 * Math.sin(t * (1.7 + i * 0.45) + i * 1.3)));
    const f = fall.value;
    return {
      opacity: dim.value,
      borderTopLeftRadius: wobble(0),
      borderTopRightRadius: wobble(1),
      borderBottomRightRadius: wobble(2),
      borderBottomLeftRadius: wobble(3),
      transform: [
        { translateY: f * size * 2.2 },
        { scale: (0.78 + l * 0.32) * (1 - f * 0.95) },
        { rotate: `${t * 14}deg` },
      ],
    };
  });
  const disc: ViewStyle = { width: size, height: size, borderRadius: size / 2, backgroundColor: colors.record, position: "absolute" };
  return (
    <View style={{ width: size, height: size, alignItems: "center", justifyContent: "center" }} accessibilityElementsHidden>
      <Halo index={1} disc={disc} size={size} time={time} lv={lv} fall={fall} dim={dim} />
      <Halo index={0} disc={disc} size={size} time={time} lv={lv} fall={fall} dim={dim} />
      <Animated.View style={[disc, core]} />
    </View>
  );
}

function Halo({
  index,
  disc,
  size,
  time,
  lv,
  fall,
  dim,
}: {
  index: number;
  disc: ViewStyle;
  size: number;
  time: SharedValue<number>;
  lv: SharedValue<number>;
  fall: SharedValue<number>;
  dim: SharedValue<number>;
}) {
  const animated = useAnimatedStyle(() => {
    const t = time.value;
    const l = lv.value;
    const r = size * 0.06 * (0.3 + l);
    return {
      opacity: (1 - fall.value) * dim.value * (index === 0 ? 0.28 : 0.16),
      transform: [
        { translateX: Math.cos(t * (0.9 + index * 0.5) + index * 2) * r },
        { translateY: Math.sin(t * (1.1 + index * 0.4) + index) * r },
        { scale: 1 + l * (0.35 + index * 0.25) + 0.04 * Math.sin(t * 2 + index) },
      ],
    };
  });
  return <Animated.View style={[disc, animated]} />;
}

// Listening: a ring that keeps expanding out of the dot, like a ripple.
export function ListeningDot({ size = 10 }: { size?: number }) {
  const { colors } = useTheme();
  const reduced = useReducedMotion();
  const ring = useSharedValue(0);
  useEffect(() => {
    if (reduced) return;
    ring.set(withRepeat(withTiming(1, { duration: 1200, easing: Easing.out(Easing.quad) }), -1));
    return () => cancelAnimation(ring);
  }, [ring, reduced]);
  const ringStyle = useAnimatedStyle(() => ({
    opacity: 0.6 * (1 - ring.value),
    transform: [{ scale: 1 + ring.value * 1.8 }],
  }));
  const dot = { width: size, height: size, borderRadius: size / 2, backgroundColor: colors.record };
  return (
    <View style={{ width: size, height: size }}>
      <Animated.View style={[dot, { position: "absolute" }, ringStyle]} />
      <View style={dot} />
    </View>
  );
}

// Speaking: three bars moving like a voice, out of step with each other.
export function SpeakingBars({ color, height = 16 }: { color?: string; height?: number }) {
  const { colors } = useTheme();
  const reduced = useReducedMotion();
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 3, height }}>
      {[0, 1, 2].map((i) => (
        <Bar key={i} index={i} height={height} color={color ?? colors.record} still={reduced} />
      ))}
    </View>
  );
}

function Bar({ index, height, color, still }: { index: number; height: number; color: string; still: boolean }) {
  const h = useSharedValue(0.4);
  useEffect(() => {
    if (still) return;
    const d = 260 + index * 90;
    h.set(withDelay(
      index * 120,
      withRepeat(withSequence(withTiming(1, { duration: d }), withTiming(0.3, { duration: d })), -1, true),
    ));
    return () => cancelAnimation(h);
  }, [h, index, still]);
  const animated = useAnimatedStyle(() => ({ transform: [{ scaleY: h.value }] }));
  return <Animated.View style={[{ width: 3, height, borderRadius: 1.5, backgroundColor: color }, animated]} />;
}

const styles = StyleSheet.create({
  inkRow: { flexDirection: "row", flexWrap: "wrap" },
});
