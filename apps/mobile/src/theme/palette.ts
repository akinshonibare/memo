// The colours memo draws with (docs/brand.md, ADR 0031). A palette is plain data: two
// hand-tuned Forest palettes, and every other theme derived from a base hue and an
// accent hue in OKLCH, where equal steps of lightness look equal. A derived palette
// is checked for contrast before it is used, so no hue a person picks can make memo
// unreadable. Nothing here imports React: it runs anywhere, including a node check.

export type Scheme = "light" | "dark";

export type Palette = {
  scheme: Scheme;
  bg: string;
  surface: string;
  surfaceAlt: string;
  hairline: string;
  text: string;
  muted: string;
  faint: string;
  // Secondary text on a surface filled with `text` (the inverted, primary pills).
  inverseMuted: string;
  forest: string;
  record: string; // the one accent: what is live (record, today, a voice session)
  recordRing: string; // the halo round the record button
  green: string; // moss: quiet positive states
  danger: string;
  dangerSurface: string;
};

export type ThemeId = "forest" | "tide" | "clay" | "plum" | "ink" | "custom";
export type Mode = "system" | "light" | "dark";

export type ThemePrefs = {
  mode: Mode;
  theme: ThemeId;
  // Used when theme is "custom"; presets carry their own.
  hue: number;
  accent: number;
};

export const DEFAULT_PREFS: ThemePrefs = { mode: "system", theme: "forest", hue: 160, accent: 70 };

// Forest, dark: the palette memo shipped with. Unchanged, so nobody's app looks
// different until they choose it to.
const FOREST_DARK: Palette = {
  scheme: "dark",
  bg: "#0B1410",
  surface: "#15221C",
  surfaceAlt: "#1E2F27",
  hairline: "#26382F",
  text: "#F1ECDF",
  muted: "#9FAEA4",
  faint: "#627368",
  inverseMuted: "#1F5A40",
  forest: "#1F5A40",
  record: "#E2A04A",
  recordRing: "#4A3317",
  green: "#8FBF96",
  danger: "#E5645A",
  dangerSurface: "#2B1A16",
};

// Forest, light: cream paper and deep-green ink. The amber is darker than in the dark
// palette because #E2A04A on cream is about 2:1 - a record button you can't find.
const FOREST_LIGHT: Palette = {
  scheme: "light",
  bg: "#F4F0E6",
  surface: "#EAE5D8",
  surfaceAlt: "#DFD9CA",
  hairline: "#D3CCBB",
  text: "#14261D",
  muted: "#55665C",
  faint: "#8A968E",
  inverseMuted: "#B7C8BD",
  forest: "#2A6B4D",
  record: "#B06A12",
  recordRing: "#EDD6B0",
  green: "#3F7A4C",
  danger: "#B23F36",
  dangerSurface: "#F3DCD6",
};

type Preset = { label: string; hue: number; accent: number; chroma: number };

// Chroma scales how tinted the neutrals are; Ink is nearly grey.
export const PRESETS: Record<Exclude<ThemeId, "custom">, Preset> = {
  forest: { label: "Forest", hue: 160, accent: 70, chroma: 1 },
  tide: { label: "Tide", hue: 240, accent: 60, chroma: 1 },
  clay: { label: "Clay", hue: 40, accent: 165, chroma: 1 },
  plum: { label: "Plum", hue: 325, accent: 85, chroma: 1 },
  ink: { label: "Ink", hue: 250, accent: 70, chroma: 0.3 },
};

export const THEME_ORDER: ThemeId[] = ["forest", "tide", "clay", "plum", "ink", "custom"];

// --- OKLCH to sRGB (Björn Ottosson's OKLab) -----------------------------------------

type Rgb = [number, number, number]; // linear-light, 0..1

function oklchToLinear(l: number, c: number, hueDeg: number): Rgb {
  const h = (hueDeg * Math.PI) / 180;
  const a = c * Math.cos(h);
  const b = c * Math.sin(h);
  const l_ = (l + 0.3963377774 * a + 0.2158037573 * b) ** 3;
  const m_ = (l - 0.1055613458 * a - 0.0638541728 * b) ** 3;
  const s_ = (l - 0.0894841775 * a - 1.291485548 * b) ** 3;
  const clamp = (v: number) => Math.min(1, Math.max(0, v));
  return [
    clamp(4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_),
    clamp(-1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_),
    clamp(-0.0041960863 * l_ - 0.7034186147 * m_ + 1.707614701 * s_),
  ];
}

function toHex([r, g, b]: Rgb): string {
  const enc = (v: number) => {
    const s = v <= 0.0031308 ? 12.92 * v : 1.055 * v ** (1 / 2.4) - 0.055;
    return Math.round(s * 255)
      .toString(16)
      .padStart(2, "0");
  };
  return `#${enc(r)}${enc(g)}${enc(b)}`.toUpperCase();
}

function fromHex(hex: string): Rgb {
  const n = parseInt(hex.slice(1), 16);
  const dec = (v: number) => {
    const s = v / 255;
    return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return [dec((n >> 16) & 255), dec((n >> 8) & 255), dec(n & 255)];
}

export function oklch(l: number, c: number, h: number): string {
  return toHex(oklchToLinear(l, c, h));
}

// WCAG 2 contrast ratio between two hex colours.
export function contrast(a: string, b: string): number {
  const lum = ([r, g, bl]: Rgb) => 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  const la = lum(fromHex(a)) + 0.05;
  const lb = lum(fromHex(b)) + 0.05;
  return la > lb ? la / lb : lb / la;
}

// Walk a colour's lightness away from the background until it reaches the ratio.
function guard(l: number, c: number, h: number, against: string, ratio: number, scheme: Scheme): string {
  let light = l;
  let hex = oklch(light, c, h);
  for (let i = 0; i < 40 && contrast(hex, against) < ratio; i++) {
    light += scheme === "dark" ? 0.015 : -0.015;
    hex = oklch(light, c, h);
  }
  return hex;
}

// Text needs 4.5:1 (WCAG AA); the accent and faint marks are UI, which needs 3:1.
export const MIN_TEXT = 4.5;
export const MIN_UI = 3;

export function derive(hue: number, accent: number, scheme: Scheme, chroma = 1): Palette {
  const k = chroma;
  if (scheme === "dark") {
    const bg = oklch(0.17, 0.018 * k, hue);
    const surface = oklch(0.215, 0.022 * k, hue);
    const text = oklch(0.94, 0.02, 85); // cream, never white
    return {
      scheme,
      bg,
      surface,
      surfaceAlt: oklch(0.26, 0.026 * k, hue),
      hairline: oklch(0.29, 0.022 * k, hue),
      text,
      muted: guard(0.73, 0.028 * k, hue, bg, MIN_TEXT, scheme),
      faint: guard(0.53, 0.03 * k, hue, bg, MIN_UI, scheme),
      inverseMuted: oklch(0.42, 0.07 * k, hue),
      forest: oklch(0.42, 0.07 * k, hue),
      // Checked against the surface, the lower-contrast of the two it sits on.
      record: guard(0.78, 0.14, accent, surface, MIN_UI, scheme),
      recordRing: oklch(0.33, 0.06, accent),
      green: oklch(0.77, 0.07, 150),
      danger: "#E5645A",
      dangerSurface: oklch(0.23, 0.035, 25),
    };
  }
  const bg = oklch(0.96, 0.014 * k, hue);
  const surface = oklch(0.925, 0.02 * k, hue);
  return {
    scheme,
    bg,
    surface,
    surfaceAlt: oklch(0.885, 0.024 * k, hue),
    hairline: oklch(0.855, 0.02 * k, hue),
    text: oklch(0.25, 0.035 * k, hue),
    muted: guard(0.48, 0.035 * k, hue, bg, MIN_TEXT, scheme),
    faint: guard(0.64, 0.03 * k, hue, bg, MIN_UI - 0.6, scheme),
    inverseMuted: oklch(0.83, 0.03 * k, hue),
    forest: oklch(0.47, 0.08 * k, hue),
    record: guard(0.62, 0.14, accent, surface, MIN_UI, scheme),
    recordRing: oklch(0.89, 0.05, accent),
    green: oklch(0.52, 0.08, 150),
    danger: "#B23F36",
    dangerSurface: oklch(0.93, 0.03, 25),
  };
}

export function resolveScheme(mode: Mode, system: string | null | undefined): Scheme {
  if (mode === "light" || mode === "dark") return mode;
  return system === "light" ? "light" : "dark"; // memo was born dark; unknown stays dark
}

export function paletteFor(prefs: ThemePrefs, scheme: Scheme): Palette {
  if (prefs.theme === "forest") return scheme === "dark" ? FOREST_DARK : FOREST_LIGHT;
  if (prefs.theme === "custom") return derive(prefs.hue, prefs.accent, scheme);
  const p = PRESETS[prefs.theme];
  return derive(p.hue, p.accent, scheme, p.chroma);
}

// The swatch a theme chip shows: its background and accent, in the current scheme.
export function swatch(theme: ThemeId, prefs: ThemePrefs, scheme: Scheme): { bg: string; accent: string } {
  const p = paletteFor({ ...prefs, theme }, scheme);
  return { bg: p.surfaceAlt, accent: p.record };
}

// Stored prefs come from an older build, or were edited by hand: anything unknown
// falls back to the default rather than throwing at launch.
export function parsePrefs(raw: string | null): ThemePrefs {
  if (!raw) return DEFAULT_PREFS;
  try {
    const v = JSON.parse(raw) as Partial<ThemePrefs>;
    const mode: Mode = v.mode === "light" || v.mode === "dark" ? v.mode : "system";
    const theme: ThemeId = THEME_ORDER.includes(v.theme as ThemeId) ? (v.theme as ThemeId) : "forest";
    const hue = Number.isFinite(v.hue) ? ((Number(v.hue) % 360) + 360) % 360 : DEFAULT_PREFS.hue;
    const accent = Number.isFinite(v.accent) ? ((Number(v.accent) % 360) + 360) % 360 : DEFAULT_PREFS.accent;
    return { mode, theme, hue, accent };
  } catch {
    return DEFAULT_PREFS;
  }
}
