import { StyleSheet } from "react-native";

// Pure black, one serif for display text, the system sans for everything else,
// and pills instead of rectangles. Surfaces are iOS's dark greys so the app reads
// as native rather than themed.
export const colors = {
  bg: "#000000",
  surface: "#1c1c1e",
  surfaceAlt: "#2c2c2e",
  hairline: "#2a2a2c",
  text: "#ffffff",
  muted: "#98989d",
  faint: "#636366",
  record: "#ff3b30",
  green: "#30d158",
  danger: "#ff453a",
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
