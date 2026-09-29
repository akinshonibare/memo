import { StyleSheet } from "react-native";

// Forest (docs/brand.md): blacks tinted deep green, cream instead of white, one serif
// for display text and the system sans for everything else, pills not rectangles.
// Amber is the single warm accent - the dot in the icon - and it marks what is live:
// the record button, today, a voice session.
export const colors = {
  bg: "#0B1410",
  surface: "#15221C",
  surfaceAlt: "#1E2F27",
  hairline: "#26382F",
  forest: "#1F5A40",
  text: "#F1ECDF",
  muted: "#9FAEA4",
  faint: "#627368",
  record: "#E2A04A", // amber: the accent
  green: "#8FBF96", // moss
  danger: "#E5645A",
  dangerSurface: "#2B1A16",
};

export const fonts = { serif: "InstrumentSerif_400Regular" };

export const ui = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg },
  pad: { paddingHorizontal: 20 },
  h1: {
    fontFamily: fonts.serif,
    fontSize: 38,
    lineHeight: 44,
    color: colors.text,
    marginTop: 12,
    marginBottom: 16,
  },
  h2: {
    fontFamily: fonts.serif,
    fontSize: 30,
    lineHeight: 34,
    color: colors.text,
  },
  meta: { color: colors.muted, fontSize: 13 },
  rowTitle: { color: colors.text, fontSize: 17, fontWeight: "600", marginTop: 2 },
  body: { color: colors.text, fontSize: 16, lineHeight: 23 },
  bodyMuted: { color: colors.muted, fontSize: 15, lineHeight: 21, marginTop: 3 },
  label: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "600",
    letterSpacing: 0.6,
    textTransform: "uppercase",
    marginBottom: 8,
  },
  hairline: { height: StyleSheet.hairlineWidth, backgroundColor: colors.hairline },
  input: {
    fontSize: 16,
    color: colors.text,
    paddingHorizontal: 18,
    paddingVertical: 14,
    borderRadius: 24,
    backgroundColor: colors.surface,
  },
  empty: { color: colors.muted, fontSize: 15, textAlign: "center", lineHeight: 22 },
  danger: { color: colors.danger, fontSize: 14, lineHeight: 20 },
});
