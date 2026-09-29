import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, type LayoutChangeEvent, Pressable, Text, View } from "react-native";
import Animated, { FadeIn, ZoomIn } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import type { Api, MapKind, MapNode, MindMap } from "../api";
import { asTranslation, openInBible } from "../bible";
import { IconCircle, Pill } from "../components";
import { haptic } from "../haptics";
import { makeStyles, useTheme } from "../theme";
import { useUi } from "../ui";

// The map of one thing (ADR 0049): what memo has linked to a person, a note, an idea
// or a prayer, around it. Tap a neighbour to put it in the middle; hold it to open it.
// Back walks back through the middles you visited. Drawn with plain views - a line is
// a thin view, rotated - so it needs no drawing library and ships as an OTA update.

type Center = { kind: MapKind; id: string };

const ICONS: Record<MapNode["kind"], keyof typeof Ionicons.glyphMap> = {
  person: "person-outline",
  note: "document-text-outline",
  idea: "bulb-outline",
  prayer: "heart-outline",
  passage: "book-outline",
  todo: "checkbox-outline",
  event: "calendar-outline",
};
const NODE_W = 104;
const NODE_H = 58;
const CENTER_W = 140;

export function MapScreen({
  api,
  start,
  translation,
  onBack,
  onOpenNote,
  onOpenPerson,
}: {
  api: Api;
  start: Center;
  translation?: string;
  onBack: () => void;
  onOpenNote: (captureId: string) => void;
  onOpenPerson: (personId: string) => void;
}) {
  const ui = useUi();
  const styles = useStyles();
  const { colors } = useTheme();
  const insets = useSafeAreaInsets();
  const [trail, setTrail] = useState<Center[]>([start]);
  const [map, setMap] = useState<MindMap | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [size, setSize] = useState<{ w: number; h: number } | null>(null);
  const here = trail[trail.length - 1] ?? start;

  const load = useCallback(async () => {
    try {
      setMap(await api.getMap(here.kind, here.id));
      setError(null);
    } catch (e) {
      setError(String(e));
    }
  }, [api, here.kind, here.id]);
  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load]);

  const back = () => {
    if (trail.length > 1) {
      haptic.tap();
      setMap(null);
      setTrail((t) => t.slice(0, -1));
    } else onBack();
  };

  // Open the thing itself: a person's page, a note, a passage in the Bible app.
  const open = (node: MapNode) => {
    if (node.kind === "person") return onOpenPerson(node.id);
    if (node.kind === "passage" && node.passage) return openInBible(node.passage, asTranslation(translation));
    if (node.kind === "note") return onOpenNote(node.id);
    if (node.capture_id) return onOpenNote(node.capture_id);
  };
  const tap = (node: MapNode) => {
    haptic.tap();
    if (node.centrable) {
      setMap(null);
      setTrail((t) => [...t, { kind: node.kind as MapKind, id: node.id }]);
    } else open(node);
  };

  const onLayout = (e: LayoutChangeEvent) => {
    const { width, height } = e.nativeEvent.layout;
    setSize({ w: width, h: height });
  };

  const links = map?.links ?? [];
  // An ellipse: phones are tall, so the neighbours spread further up and down. Offset
  // by half a step so none sits level with the middle card (it would touch it); one or
  // two go straight above and below.
  const step = (2 * Math.PI) / Math.max(links.length, 1);
  const first = links.length <= 2 ? -Math.PI / 2 : -Math.PI / 2 + step / 2;
  const places =
    size &&
    links.map((_, i) => {
      const angle = first + i * step;
      const rx = size.w / 2 - NODE_W / 2 - 4;
      const ry = size.h / 2 - NODE_H / 2 - 8;
      // A squarer curve than an ellipse (a superellipse), so neighbours near the top
      // and bottom move out towards the corners instead of touching each other.
      const bend = (v: number) => Math.sign(v) * Math.abs(v) ** 0.6;
      return { x: size.w / 2 + rx * bend(Math.cos(angle)), y: size.h / 2 + ry * bend(Math.sin(angle)) };
    });

  return (
    <View style={ui.screen}>
      <View style={[styles.topBar, ui.pad]}>
        <IconCircle name="chevron-back" size={38} onPress={back} accessibilityLabel="Back" />
        <Text style={styles.title}>Map</Text>
      </View>
      {error && <Text style={[ui.danger, ui.pad]}>{error}</Text>}
      <View style={[styles.canvas, { marginBottom: insets.bottom + 12 }]} onLayout={onLayout}>
        {!map || !size || !places ? (
          <ActivityIndicator color={colors.muted} style={{ flex: 1 }} />
        ) : (
          <>
            {places.map((p, i) => {
              const dx = p.x - size.w / 2;
              const dy = p.y - size.h / 2;
              const length = Math.hypot(dx, dy);
              return (
                <Animated.View
                  key={`line-${map.links[i]?.node.kind}-${map.links[i]?.node.id}`}
                  entering={FadeIn.duration(300).delay(60 + i * 25)}
                  style={[
                    styles.line,
                    {
                      width: length,
                      left: size.w / 2 + dx / 2 - length / 2,
                      top: size.h / 2 + dy / 2,
                      transform: [{ rotate: `${Math.atan2(dy, dx)}rad` }],
                    },
                  ]}
                />
              );
            })}
            {map.links.map((link, i) => {
              const p = places[i];
              if (!p) return null;
              return (
                <Animated.View
                  key={`${link.node.kind}-${link.node.id}`}
                  entering={ZoomIn.duration(260).delay(80 + i * 30)}
                  style={[styles.nodeWrap, { left: p.x - NODE_W / 2, top: p.y - NODE_H / 2 }]}
                >
                  <Pressable
                    onPress={() => tap(link.node)}
                    onLongPress={() => open(link.node)}
                    accessibilityRole="button"
                    accessibilityLabel={`${link.relation}: ${link.node.label}`}
                    accessibilityHint={link.node.centrable ? "Puts it in the middle. Hold to open it." : "Opens it."}
                    style={({ pressed }) => [styles.node, pressed && { opacity: 0.6 }]}
                  >
                    <View style={styles.nodeHead}>
                      <Ionicons name={ICONS[link.node.kind]} size={13} color={colors.muted} />
                      <Text style={styles.relation} numberOfLines={1}>
                        {link.relation}
                      </Text>
                    </View>
                    <Text style={styles.nodeLabel} numberOfLines={2}>
                      {link.node.label}
                    </Text>
                  </Pressable>
                </Animated.View>
              );
            })}
            <Animated.View
              key={`center-${map.center.kind}-${map.center.id}`}
              entering={ZoomIn.duration(300)}
              style={[styles.centerWrap, { left: size.w / 2 - CENTER_W / 2, top: size.h / 2 - 62 }]}
            >
              <View style={styles.center}>
                <Ionicons name={ICONS[map.center.kind]} size={18} color={colors.text} />
                <Text style={styles.centerLabel} numberOfLines={3}>
                  {map.center.label}
                </Text>
                {map.links.length === 0 ? (
                  <Text style={ui.meta}>Nothing linked yet</Text>
                ) : (
                  <Pill label="Open" onPress={() => open(map.center)} />
                )}
              </View>
            </Animated.View>
          </>
        )}
      </View>
    </View>
  );
}

const useStyles = makeStyles((colors) => ({
  topBar: { flexDirection: "row", alignItems: "center", gap: 10, paddingTop: 4 },
  title: { fontFamily: "InstrumentSerif_400Regular", fontSize: 30, color: colors.text, marginLeft: 4 },
  canvas: { flex: 1, marginHorizontal: 8, marginTop: 8 },
  line: { position: "absolute", height: 1, backgroundColor: colors.hairline },
  nodeWrap: { position: "absolute", width: NODE_W, height: NODE_H },
  node: {
    flex: 1,
    backgroundColor: colors.surface,
    borderRadius: 14,
    paddingHorizontal: 8,
    paddingVertical: 6,
    justifyContent: "center",
    gap: 2,
  },
  nodeHead: { flexDirection: "row", alignItems: "center", gap: 4 },
  relation: { color: colors.muted, fontSize: 10, flexShrink: 1 },
  nodeLabel: { color: colors.text, fontSize: 12, lineHeight: 15 },
  centerWrap: { position: "absolute", width: CENTER_W, alignItems: "center" },
  center: {
    width: CENTER_W,
    backgroundColor: colors.surfaceAlt,
    borderRadius: 20,
    borderWidth: 1,
    borderColor: colors.faint,
    padding: 12,
    alignItems: "center",
    gap: 6,
  },
  centerLabel: { color: colors.text, fontSize: 15, fontWeight: "600", textAlign: "center" },
}));
