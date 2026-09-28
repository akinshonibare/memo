import { StyleSheet } from "react-native";

export const colors = {
  bg: "#0f1115",
  surface: "#1b1f27",
  surfaceAlt: "#232835",
  text: "#e8eaf0",
  muted: "#8b93a7",
  accent: "#3b82f6",
  accentSoft: "#1e3a5f",
  danger: "#dc2626",
};

export const ui = StyleSheet.create({
  screen: { flex: 1, backgroundColor: colors.bg, padding: 16, gap: 8 },
  card: {
    backgroundColor: colors.surface,
    borderRadius: 12,
    padding: 12,
    marginBottom: 8,
  },
  cardTitle: { color: colors.text, fontSize: 16, marginBottom: 4 },
  cardBody: { color: colors.text, fontSize: 15, lineHeight: 21 },
  cardMeta: { color: colors.muted, fontSize: 12 },
  button: {
    backgroundColor: colors.accent,
    paddingVertical: 12,
    paddingHorizontal: 16,
    borderRadius: 12,
    alignItems: "center",
  },
  buttonDisabled: { opacity: 0.4 },
  buttonText: { color: "#fff", fontWeight: "600" },
  empty: { color: colors.muted, textAlign: "center", marginTop: 32 },
  label: { color: colors.muted, fontSize: 12, marginBottom: 4 },
  link: { color: colors.accent, marginBottom: 8 },
});
