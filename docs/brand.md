# memo — brand

**Mark:** a lowercase serif **m** followed by an amber full stop: `m.` It reads as the
end of a thought written down, and as a record light. The wordmark in the app is
`memo.` with the same amber dot.

**Type:** Instrument Serif for display (the wordmark, screen titles); the iOS system
font for everything else.

**Colour** (`apps/mobile/src/theme/palette.ts`, ADR 0031). Forest is the default and
the palette below is its dark scheme, unchanged from the first release:

| Token | Hex | Use |
| --- | --- | --- |
| Deep forest | `#0B241A` | Icon and splash background |
| bg | `#0B1410` | App background: black, tinted green |
| surface / surfaceAlt | `#15221C` / `#1E2F27` | Cards, sheets, inputs |
| text | `#F1ECDF` | Cream, never pure white |
| muted / faint | `#9FAEA4` / `#627368` | Secondary text |
| **amber** (`record`) | `#E2A04A` | The one warm accent. It marks what is live: record, today, a voice session |
| moss (`green`) | `#8FBF96` | Quiet positive states |
| danger | `#E5645A` | Destructive actions only |

**Forest, light** (when the phone is in light mode, or Light is chosen): paper `#F4F0E6`,
surfaces `#EAE5D8` / `#DFD9CA`, ink `#14261D`, muted `#55665C`, and amber darkened to
`#B06A12`, because `#E2A04A` on cream is about 2:1.

**Other themes** (Tide, Clay, Plum, Ink, Custom) are derived from a base hue and an
accent hue in OKLCH, with every text colour held to 4.5:1 and the accent to 3:1 against
the surfaces it sits on. A theme can change the hues, never the rules below.

**Rules:** one accent, used sparingly. If everything is amber, nothing is live. No
pure black or pure white anywhere, in any theme.

**Motion** (`apps/mobile/src/motion.tsx`, ADR 0032): motion means one thing each time,
paired with the haptic of the same meaning. A tap squeezes, something starting swells,
something done lands and settles once, a choice glides. One signature animation per
screen:

- the amber full stop in `memo.` breathes while a note is processing
- the record orb follows your voice and drops into a full stop when saved
- a note's words ink in when they first arrive
- ticking something off sends up fireflies, a swarm for the last thing on Today; the
  particles follow the season (leaves, snow, petals)

**Delight** (ADR 0041): rare, and never a score. A milestone note, memo's birthday, a
"Still up?" after 1am, and two hidden toys (tap the dot; search "fireflies"). No streaks,
no badges, nothing that counts against you.

Springs, not durations. With Reduce Motion on, decorative motion doesn't run and the
rest becomes a plain change.

**Assets:** `scripts/brand_assets.py` regenerates the icon, splash and Android icons
from the mark: `uv run --with pillow python scripts/brand_assets.py`.
